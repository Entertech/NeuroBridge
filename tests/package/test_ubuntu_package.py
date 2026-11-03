from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ubuntu_package", ROOT / "tools/build-ubuntu-package.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class UbuntuPackageTests(unittest.TestCase):
    def test_wheel_versions_must_follow_the_runtime_requirements(self):
        builder.validate_runtime_lock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "requirements.lock").write_text("websockets==99.0\n")
            with mock.patch.object(builder, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "complete Linux requirements"):
                    builder.validate_runtime_lock()

    def test_wheel_cache_fails_closed_for_tampering_and_never_resolves_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "fixture.whl"
            wheel.write_bytes(b"verified wheel fixture")
            lock = root / "inputs.toml"
            lock.write_text(f'[[wheels]]\nfilename="fixture.whl"\nsha256="{hashlib.sha256(wheel.read_bytes()).hexdigest()}"\nurl="https://files.pythonhosted.org/fixture.whl"\n')
            with mock.patch.object(builder, "LOCK", lock), mock.patch.object(builder.urllib.request, "urlopen") as network:
                self.assertEqual(len(builder.wheels(root)), 1)
                wheel.write_bytes(b"corruption")
                with self.assertRaisesRegex(ValueError, "invalid locked wheel"):
                    builder.wheels(root, download=True)
                wheel.unlink()
                with self.assertRaisesRegex(ValueError, "Missing"):
                    builder.wheels(root)
                network.assert_not_called()

    def test_packaging_rejects_a_different_os_or_python_abi(self):
        with mock.patch.object(builder.sys, "platform", "darwin"):
            with self.assertRaisesRegex(ValueError, "Ubuntu 24.04"):
                builder.require_host()

    def test_control_declares_runtime_only_dependencies_and_complete_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            builder.stage_control(stage, "1.2.3")
            control = (stage / "DEBIAN/control").read_text()
            for dependency in ("python3.12", "libstdc++6", "bluez", "dbus", "iproute2", "dnsmasq-base", "logrotate", "systemd"):
                self.assertIn(dependency, control)
            for build_dependency in ("pip", "cmake", "g++", "libeigen3-dev"):
                self.assertNotIn(build_dependency, control)
            self.assertIn("Architecture: amd64", control)
            self.assertIn("Version: 1.2.3", control)
            self.assertEqual((stage / "DEBIAN/conffiles").read_text(), "/etc/logrotate.d/neurobridge\n")
            for name in ("preinst", "postinst", "prerm", "postrm"):
                script = stage / "DEBIAN" / name
                self.assertNotIn("@COMMON@", script.read_text())
                self.assertTrue(os.access(script, os.X_OK))
                subprocess.run(["sh", "-n", str(script)], check=True)

    def test_upgrade_stops_and_recovers_only_previously_active_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            builder.stage_control(stage, "1.2.3")
            (root / "run/systemd/system").mkdir(parents=True)
            (root / "etc/systemd/system").mkdir(parents=True)
            (root / "state").mkdir()
            binaries = root / "bin"
            binaries.mkdir()
            # A stateful systemctl fixture verifies the restart contract without
            # touching the host service manager or its files.
            systemctl = binaries / "systemctl"
            systemctl.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p=Path(os.environ['SERVICE_STATE']); d=json.loads(p.read_text()); args=sys.argv[1:]
unit=args[-1]; action=args[0]
if action=='is-active': sys.exit(0 if unit in d['active'] else 3)
if action=='stop': d['active'].remove(unit)
if action=='start': d['active'].append(unit)
d['calls'].append(args); p.write_text(json.dumps(d))
''')
            systemctl.chmod(0o755)
            state = root / "service-state.json"
            state.write_text(json.dumps({"active": ["neurobridge.service"], "calls": []}))
            env = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"], "SERVICE_STATE": str(state)}
            def execute(name, action):
                source = (stage / "DEBIAN" / name).read_text()
                source = source.replace("/run/systemd/system", str(root / "run/systemd/system"))
                source = source.replace("/etc/systemd/system", str(root / "etc/systemd/system"))
                source = source.replace("/var/lib/neurobridge-package", str(root / "state"))
                target = root / name
                target.write_text(source)
                return subprocess.run(["sh", str(target), action], env=env, capture_output=True, text=True)
            self.assertEqual(execute("prerm", "upgrade").returncode, 0)
            self.assertEqual(json.loads(state.read_text())["active"], [])
            self.assertTrue((root / "state/neurobridge.service.active").exists())
            self.assertFalse((root / "state/neurobridge-dhcp.service.active").exists())
            self.assertEqual(execute("postinst", "abort-upgrade").returncode, 0)
            self.assertEqual(json.loads(state.read_text())["active"], ["neurobridge.service"])
            self.assertFalse((root / "state/neurobridge.service.active").exists())
            # Local/source deployment overrides must not be silently stopped.
            (root / "etc/systemd/system/neurobridge.service").write_text("foreign unit")
            rejected = execute("prerm", "remove")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("local unit override", rejected.stderr)
            self.assertEqual(json.loads(state.read_text())["active"], ["neurobridge.service"])


if __name__ == "__main__":
    unittest.main()
