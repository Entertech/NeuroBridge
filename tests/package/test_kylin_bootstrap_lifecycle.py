"""Run installation transactions with isolated paths and simulated OS tools."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from tests.package.test_kylin_bootstrap import BUILDER, ROOT


class Sandbox:
    def __init__(self, root: Path):
        self.root = root
        for relative in ("opt", "etc/neurobridge", "etc/systemd/system", "var/tmp",
                         "var/lib/neurobridge", "var/log/neurobridge", "dev", "stubs"):
            (root / relative).mkdir(parents=True, exist_ok=True)
        (root / "etc/os-release").write_text("ID=kylin\n")
        self.unit = root / "etc/systemd/system/neurobridge.service"
        self.app = root / "opt/neurobridge"
        self.config = root / "etc/neurobridge/gateway.toml"
        self.state_file = root / "state.json"
        self.save(active=False, enabled=False, calls=[], groups=[], fail_action="", failed=False)
        self.env = {**os.environ, "NB_SANDBOX": str(root),
                    "PATH": str(root / "stubs") + os.pathsep + os.environ["PATH"]}
        for name in ("systemctl", "getent", "id", "groupadd", "useradd", "usermod",
                     "stat", "uname", "chown", "install"):
            path = root / "stubs" / name
            path.write_text(f"#!{sys.executable}\n" + SYSTEM_STUB)
            path.chmod(0o755)

    def state(self) -> dict:
        return json.loads(self.state_file.read_text())

    def save(self, **changes) -> None:
        state = self.state() if self.state_file.exists() else {}
        state.update(changes)
        self.state_file.write_text(json.dumps(state))

    def relocate(self, text: str) -> str:
        # Only paths and root/OS guards change; transaction control flow is real.
        text = text.replace('[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "Run as root."', ":")
        text = text.replace("${ID,,}", "${ID}")
        return re.sub(r"/(?:opt|etc|var|dev|usr/lib)/", lambda match: str(self.root) + match[0], text)

    def unit_text(self, marker: str) -> str:
        return f"# {marker}\n[Service]\nExecStart={self.app}/runtime/bin/python -m neurobridge\n"

    def existing(self, *, active: bool, enabled: bool) -> None:
        self.app.mkdir()
        (self.app / "version.txt").write_text("old-code")
        self.unit.write_text(self.unit_text("old-unit"))
        self.config.write_text("现场配置\n")
        self.save(active=active, enabled=enabled)

    def device(self, group: str = "usb-serial") -> None:
        (self.root / "dev/ttyUSB0").symlink_to("/dev/null")
        self.env["NB_DEVICE_GROUP"] = group

    def installer(self) -> Path:
        source = self.root / "archive-source"
        for name in ("runtime/bin/python", "runtime/bin/neurobridge_affective_bridge",
                     "payload/neurobridge/__init__.py", "payload/version.txt",
                     "gateway.toml.example", "packaging/neurobridge.service"):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name.startswith("runtime/"):
                path.write_text("#!/bin/sh\nexit 0\n")
                path.chmod(0o755)
            elif name.endswith(".service"):
                path.write_text(self.unit_text("new-unit"))
            else:
                path.write_text("new-code")
        archive = self.root / "runtime.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            output.add(source, arcname=".")
        scripts = self.root / "scripts"
        scripts.mkdir()
        (scripts / "kylin-runtime-manifest.toml").write_text("fetch fixture\n")
        fetch = scripts / "fetch-runtime.sh"
        fetch.write_text(f'#!/bin/sh\nprintf "%s\\n" "{archive}"\n')
        fetch.chmod(0o755)
        script = scripts / "bootstrap-install.sh"
        script.write_text(self.relocate((ROOT / "packaging/kylin/bootstrap-install.sh").read_text()))
        return script

    def run(self, script: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(script), *args], env=self.env, text=True,
                              capture_output=True, timeout=20)


SYSTEM_STUB = r'''
import json, os, pathlib, shutil, sys
root = pathlib.Path(os.environ["NB_SANDBOX"])
state_file = root / "state.json"
state = json.loads(state_file.read_text())
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
state["calls"].append([name, *args])
code = 0
if name == "systemctl":
    action = args[0]
    if action == state.get("fail_action") and not state["failed"]:
        state["failed"] = True
        code = 1
    elif action == "is-active":
        code = 0 if state["active"] else 3
    elif action == "is-enabled":
        code = 0 if state["enabled"] else 1
    elif action == "start":
        state["active"] = True
    elif action == "stop":
        state["active"] = False
    elif action == "enable":
        state["enabled"] = True
    elif action == "disable":
        state["enabled"] = False
elif name == "usermod":
    code = int(os.environ.get("NB_DENY_GROUP", "0"))
    if not code:
        state["groups"].append(args[1])
elif name == "getent":
    code = 1 if args[-1] == "unknown-group" else 0
elif name == "stat":
    print(os.environ.get("NB_DEVICE_GROUP", "usb-serial"))
elif name == "uname":
    print("x86_64")
elif name == "id":
    print("123")
elif name == "install":
    paths, mode, directories, index = [], 0o755, "-d" in args, 0
    while index < len(args):
        if args[index] in ("-o", "-g", "-m"):
            if args[index] == "-m":
                mode = int(args[index + 1], 8)
            index += 2
        elif args[index] == "-d":
            index += 1
        else:
            paths.append(args[index])
            index += 1
    if directories:
        for path in paths:
            pathlib.Path(path).mkdir(parents=True, exist_ok=True)
            pathlib.Path(path).chmod(mode)
    else:
        shutil.copyfile(paths[0], paths[1])
        pathlib.Path(paths[1]).chmod(mode)
state_file.write_text(json.dumps(state))
sys.exit(code)
'''


class SandboxTests(unittest.TestCase):
    def sandbox(self) -> Sandbox:
        directory = tempfile.TemporaryDirectory(prefix="neurobridge-lifecycle-")
        self.addCleanup(directory.cleanup)
        return Sandbox(Path(directory.name))


class BootstrapLifecycleTests(SandboxTests):
    def test_fresh_install_grants_actual_tty_group_before_service_start(self) -> None:
        for group in ("usb-serial", "dialout"):
            with self.subTest(group=group):
                box = self.sandbox()
                box.device(group)
                result = box.run(box.installer())
                self.assertEqual(result.returncode, 0, result.stderr)
                state = box.state()
                self.assertEqual(state["groups"], [group])
                self.assertTrue(state["active"])
                self.assertTrue(state["enabled"])
                calls = state["calls"]
                self.assertLess(calls.index(["usermod", "-aG", group, "neurobridge"]),
                                calls.index(["systemctl", "start", "neurobridge.service"]))

    def test_unavailable_or_failed_serial_authorization_keeps_old_deployment(self) -> None:
        for group, denied in ((None, False), ("root", False), ("unknown-group", False),
                              ("usb-serial", True)):
            with self.subTest(group=group, denied=denied):
                box = self.sandbox()
                box.existing(active=True, enabled=True)
                if group:
                    box.device(group)
                box.env["NB_DENY_GROUP"] = str(int(denied))
                result = box.run(box.installer())
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((box.app / "version.txt").read_text(), "old-code")
                self.assertEqual(box.unit.read_text(), box.unit_text("old-unit"))
                self.assertTrue(box.state()["active"])
                self.assertNotIn(["systemctl", "stop", "neurobridge.service"], box.state()["calls"])
                self.assertNotIn("root", box.state()["groups"])

    def test_upgrade_replaces_code_and_preserves_service_states(self) -> None:
        for active, enabled in ((True, True), (True, False), (False, True), (False, False)):
            with self.subTest(active=active, enabled=enabled):
                box = self.sandbox()
                box.existing(active=active, enabled=enabled)
                box.device()
                result = box.run(box.installer())
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((box.app / "version.txt").read_text(), "new-code")
                self.assertEqual(box.config.read_text(), "现场配置\n")
                self.assertEqual(box.state()["active"], active)
                self.assertEqual(box.state()["enabled"], enabled)
                calls = box.state()["calls"]
                self.assertIn(["systemctl", "stop", "neurobridge.service"], calls)
                if active:
                    self.assertLess(calls.index(["systemctl", "stop", "neurobridge.service"]),
                                    calls.index(["systemctl", "start", "neurobridge.service"]))

    def test_failure_restores_unit_code_config_and_service_states(self) -> None:
        for active, enabled in ((True, True), (True, False), (False, True), (False, False)):
            with self.subTest(active=active, enabled=enabled):
                box = self.sandbox()
                box.existing(active=active, enabled=enabled)
                box.device()
                box.save(fail_action="daemon-reload")
                result = box.run(box.installer())
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((box.app / "version.txt").read_text(), "old-code")
                self.assertEqual(box.unit.read_text(), box.unit_text("old-unit"))
                self.assertEqual(box.config.read_text(), "现场配置\n")
                self.assertEqual(box.state()["active"], active)
                self.assertEqual(box.state()["enabled"], enabled)
                self.assertFalse(list((box.root / "opt").glob("neurobridge-previous.*")))

    def test_failed_fresh_start_removes_new_tree_unit_and_config(self) -> None:
        box = self.sandbox()
        box.device()
        box.save(fail_action="start")
        result = box.run(box.installer())
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(box.app.exists())
        self.assertFalse(box.unit.exists())
        self.assertFalse(box.config.exists())
        self.assertFalse(box.state()["active"])
        self.assertFalse(box.state()["enabled"])

    def test_failed_stop_does_not_replace_running_code(self) -> None:
        box = self.sandbox()
        box.existing(active=True, enabled=True)
        box.device()
        box.save(fail_action="stop")
        result = box.run(box.installer())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((box.app / "version.txt").read_text(), "old-code")
        self.assertTrue(box.state()["active"])


class BootstrapMaintainerTests(SandboxTests):
    def scripts(self, box: Sandbox) -> Path:
        BUILDER.write_deb_metadata(box.root)
        directory = box.root / "DEBIAN"
        for name in ("postinst", "prerm", "postrm"):
            script = directory / name
            script.write_text(box.relocate(script.read_text()))
        return directory

    def test_configure_with_old_interpreter_deploys_new_source(self) -> None:
        box = self.sandbox()
        interpreter = box.app / "runtime/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text("#!/bin/sh\nexit 0\n")
        interpreter.chmod(0o755)
        stub = box.root / "usr/lib/neurobridge-bootstrap/bootstrap-build.sh"
        stub.parent.mkdir(parents=True)
        marker = box.root / "deployed"
        stub.write_text(f'#!/bin/sh\nprintf "%s" new-code > "{marker}"\n')
        stub.chmod(0o755)
        result = box.run(self.scripts(box) / "postinst", "configure", "0.2.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_text(), "new-code")

    def test_removal_disables_autostart_and_removes_only_unit(self) -> None:
        box = self.sandbox()
        box.existing(active=True, enabled=True)
        scripts = self.scripts(box)
        result = box.run(scripts / "prerm", "remove")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(box.state()["active"])
        self.assertFalse(box.state()["enabled"])
        result = box.run(scripts / "postrm", "remove")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(box.unit.exists())
        self.assertTrue(box.app.exists())
        self.assertEqual(box.config.read_text(), "现场配置\n")
        self.assertEqual(box.run(scripts / "postrm", "purge").returncode, 0)

    def test_upgrade_hooks_and_foreign_units_are_untouched(self) -> None:
        box = self.sandbox()
        box.existing(active=True, enabled=True)
        scripts = self.scripts(box)
        self.assertEqual(box.run(scripts / "prerm", "upgrade", "new").returncode, 0)
        self.assertEqual(box.run(scripts / "postrm", "upgrade", "old").returncode, 0)
        box.unit.write_text("[Service]\nExecStart=/home/operator/gateway\n")
        self.assertEqual(box.run(scripts / "prerm", "remove").returncode, 0)
        self.assertEqual(box.run(scripts / "postrm", "remove").returncode, 0)
        self.assertTrue(box.unit.exists())
        self.assertTrue(box.state()["active"])
        self.assertTrue(box.state()["enabled"])

    def test_removal_is_refused_when_stop_fails(self) -> None:
        box = self.sandbox()
        box.existing(active=True, enabled=True)
        box.save(fail_action="stop")
        result = box.run(self.scripts(box) / "prerm", "remove")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(box.unit.exists())
        self.assertTrue(box.state()["enabled"])

    def test_rpm_upgrade_and_removal_hooks_have_the_same_lifecycle(self) -> None:
        box = self.sandbox()
        box.existing(active=True, enabled=True)
        payload = box.root / "rpm-payload"
        payload.mkdir()
        work = box.root / "rpm-work"
        spec_text = ""

        def fake_rpmbuild(args, **kwargs):
            nonlocal spec_text
            topdir = Path(args[args.index("--define") + 1].split(" ", 1)[1])
            spec_text = (topdir / "SPECS/neurobridge-bootstrap.spec").read_text()
            output = topdir / "RPMS/x86_64/test.rpm"
            output.parent.mkdir()
            output.write_bytes(b"rpm-fixture")

        with mock.patch.object(BUILDER.shutil, "which", return_value="rpmbuild"), \
                mock.patch.object(BUILDER, "run", side_effect=fake_rpmbuild):
            BUILDER.write_rpm(payload, box.root / "test.rpm", work)
        preun = box.root / "preun.sh"
        postun = box.root / "postun.sh"
        preun.write_text(box.relocate(spec_text.split("%preun\n")[1].split("%postun\n")[0]))
        postun.write_text(box.relocate(spec_text.split("%postun\n")[1].split("%files\n")[0]))
        self.assertEqual(box.run(preun, "1").returncode, 0)
        self.assertEqual(box.run(postun, "1").returncode, 0)
        self.assertTrue(box.state()["active"])
        self.assertEqual(box.run(preun, "0").returncode, 0)
        self.assertFalse(box.state()["active"])
        self.assertFalse(box.state()["enabled"])
        self.assertEqual(box.run(postun, "0").returncode, 0)
        self.assertFalse(box.unit.exists())


class PortablePythonTests(SandboxTests):
    def test_archive_setup_uses_bundled_python_despite_system_python311(self) -> None:
        for force_portable in (False, True):
            with self.subTest(force_portable=force_portable):
                box = self.sandbox()
                interpreter_source = f"#!{sys.executable}\n" + r'''
import json, os, pathlib, sys
root = pathlib.Path(os.environ["NB_SANDBOX"])
with (root / "python-calls.jsonl").open("a") as log:
    log.write(json.dumps(sys.argv) + "\n")
if sys.argv[1:3] == ["-m", "venv"]:
    venv = pathlib.Path(sys.argv[3])
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").symlink_to(pathlib.Path(sys.argv[0]).resolve())
    (venv / "lib/python3.11/site-packages").mkdir(parents=True)
elif sys.argv[1:2] == ["-c"] and "site.getsitepackages" in sys.argv[2]:
    print(pathlib.Path(sys.argv[0]).resolve().parents[1] / "lib/python3.11/site-packages")
elif "--version" in sys.argv:
    print("Python 3.11.16")
'''
                system_python = box.root / "stubs/python3.11"
                system_python.write_text(interpreter_source)
                system_python.chmod(0o755)
                portable_source = box.root / "archive-input/python/bin/python3"
                portable_source.parent.mkdir(parents=True)
                portable_source.write_text(interpreter_source)
                portable_source.chmod(0o755)
                (portable_source.parents[1] / "lib/python3.11/site-packages").mkdir(parents=True)
                archives = box.root / "python-runtime"
                archives.mkdir()
                archive = archives / "cpython-3.11.16+20260825-x86_64-unknown-linux-gnu-install_only.tar.gz"
                with tarfile.open(archive, "w:gz") as output:
                    output.add(portable_source.parents[1], arcname="python")
                (box.root / "requirements.lock").write_text("")
                (box.root / "wheelhouse").mkdir()
                wheel = box.root / "wheelhouse/test.whl"
                wheel.write_bytes(b"wheel-fixture")
                (box.root / "config").mkdir()
                (box.root / "config/kylin-wheelhouse.sha256").write_text(
                    hashlib.sha256(wheel.read_bytes()).hexdigest() + "  wheelhouse/test.whl\n"
                )
                script_dir = box.root / "linux"
                script_dir.mkdir()
                source = (ROOT / "linux/setup-kylin-python.sh").read_text()
                source = re.sub(r'portable_sha256="[0-9a-f]+"',
                                'portable_sha256="' + hashlib.sha256(archive.read_bytes()).hexdigest() + '"',
                                source)
                script = script_dir / "setup-kylin-python.sh"
                script.write_text(box.relocate(source))
                script.chmod(0o755)
                box.env["NEUROBRIDGE_BOOTSTRAP"] = "1"
                box.env.pop("NEUROBRIDGE_PORTABLE_PYTHON", None)
                if force_portable:
                    # Run the real archive builder to verify that it requests
                    # the pinned interpreter from the real setup helper.
                    for relative in ("neurobridge", "web", "packaging/kylin", "tools"):
                        (box.root / relative).mkdir(parents=True)
                    for relative in ("pyproject.toml", "sdk.lock", "config/gateway.toml.example"):
                        (box.root / relative).write_text("")
                    (box.root / "packaging/kylin/neurobridge.service").write_text("unit-fixture\n")
                    (box.root / "config/kylin-runtime-manifest.toml").write_text(
                        'file_name = "runtime.tar.gz"\nsha256 = ""\nurl = ""\n'
                    )
                    algorithm = script_dir / "setup-kylin-algorithm.sh"
                    algorithm.write_text(f'#!/bin/sh\nmkdir -p "{box.root}/.runtime/algorithm"\n'
                                         f'printf "#!/bin/sh\\nexit 0\\n" > "{box.root}/.runtime/algorithm/neurobridge_affective_bridge"\n'
                                         f'chmod 755 "{box.root}/.runtime/algorithm/neurobridge_affective_bridge"\n')
                    algorithm.chmod(0o755)
                    stage = box.root / "tools/stage-venv-packages.sh"
                    stage.write_text((ROOT / "tools/stage-venv-packages.sh").read_text())
                    stage.chmod(0o755)
                    builder = box.root / "tools/build-kylin-runtime-archive.sh"
                    builder.write_text(box.relocate((ROOT / "tools/build-kylin-runtime-archive.sh").read_text()))
                    result = box.run(builder, "--source-root", str(box.root),
                                     "--output-dir", str(box.root / "output"))
                    self.assertTrue((box.root / "output/runtime.tar.gz").exists(), result.stderr)
                else:
                    result = box.run(script)
                self.assertEqual(result.returncode, 0, result.stderr)
                portable = archives / "python/bin/python3"
                self.assertEqual(portable.exists(), force_portable)
                self.assertEqual((box.root / ".venv/bin/python").resolve(),
                                 portable.resolve() if force_portable else system_python.resolve())
                calls = [json.loads(line) for line in (box.root / "python-calls.jsonl").read_text().splitlines()]
                if force_portable:
                    self.assertFalse(any(call[0] == str(system_python) for call in calls))


if __name__ == "__main__":
    unittest.main()
