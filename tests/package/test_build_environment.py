"""Exercise rejected downloads, drift, closed dependencies and CI consumers.

No application package or remote workflow is built by these tests.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import build_environment as build
from tools import check_build_environment as policy
from tools.check_source_train_workflows import load_workflows


class BuildInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = b"reviewed tool input"
        self.item = dict(filename="tool.zip", url="https://example.com/tool-1.2.3.zip",
                         sha256=hashlib.sha256(self.data).hexdigest())

    def test_download_is_verified_before_becoming_a_cache_entry(self):
        with mock.patch.object(build.urllib.request, "urlopen", return_value=io.BytesIO(self.data)):
            path = build.fetch(self.item, self.root)
        self.assertEqual(path.read_bytes(), self.data)
        path.write_bytes(b"corrupt")
        with mock.patch.object(build.urllib.request, "urlopen") as network:
            with self.assertRaisesRegex(ValueError, "Cached.*digest mismatch"):
                build.fetch(self.item, self.root)
        network.assert_not_called()

    def test_failed_or_corrupt_download_leaves_no_accepted_or_partial_input(self):
        for failure in (False, True):
            with self.subTest(network_failure=failure):
                kwargs = {"side_effect": OSError("unavailable")} if failure else {"return_value": io.BytesIO(b"wrong")}
                with mock.patch.object(build.urllib.request, "urlopen", **kwargs):
                    with self.assertRaises((OSError, ValueError)):
                        build.fetch(self.item, self.root)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_zip_traversal_and_symlinks_are_rejected(self):
        for member in ("../escape", "/absolute", "link"):
            archive = self.root / "input.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                info = zipfile.ZipInfo(member)
                if member == "link":
                    info.external_attr = 0o120777 << 16
                stream.writestr(info, b"data")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                build.extract(archive, self.root / "destination")
        self.assertFalse((self.root / "escape").exists())

    def test_host_image_python_and_architecture_are_all_required(self):
        lock = build.load_lock()
        env = {"ImageOS": "ubuntu22", "ImageVersion": lock["runner_images"]["ubuntu22"]["version"], "RUNNER_ARCH": "X64"}
        with mock.patch.object(build.platform, "python_version", return_value=lock["python"]["linux"]):
            build.check_host(env)
            for field, value in (("ImageVersion", "new-image"), ("ImageOS", "unknown"), ("RUNNER_ARCH", "ARM64")):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    build.check_host(env | {field: value})
        with mock.patch.object(build.platform, "python_version", return_value="3.11.0"), self.assertRaisesRegex(ValueError, "Python drift"):
            build.check_host(env)

    def test_python_install_uses_hashes_and_never_resolves_editable_build_dependencies(self):
        with mock.patch.object(build, "fetch", return_value=self.root / "pip.whl"), \
             mock.patch.object(build, "pip_command") as pip, mock.patch.object(build, "write_evidence"):
            build.python_dependencies(runtime=True, ci=True, editable=True)
        args = pip.call_args_list[0].args
        self.assertIn("--require-hashes", args)
        self.assertIn("--only-binary=:all:", args)
        self.assertIn(str(build.ROOT / "requirements-runtime-build.lock"), args)
        editable = pip.call_args_list[1].args
        self.assertIn("--no-build-isolation", editable)
        self.assertIn("--no-deps", editable)
        self.assertEqual(pip.call_args_list[2].args[1:], ("check",))

    def test_snapshot_failure_stops_before_install_and_never_uses_live_sources(self):
        calls = []
        def execute(args):
            calls.append(args)
            sources = next(arg.split("=", 1)[1] for arg in args if arg.startswith("Dir::Etc::sourcelist="))
            content = Path(sources).read_text()
            self.assertIn("snapshot.ubuntu.com/ubuntu/" + build.load_lock()["apt"]["snapshot"], content)
            self.assertIn("signed-by=", content)
            self.assertIn("Dir::Etc::sourceparts=-", args)
            self.assertIn("APT::Update::Error-Mode=any", args)
            raise subprocess.CalledProcessError(100, args)
        with mock.patch.object(build, "check_host"), mock.patch.dict(build.os.environ, {"ImageOS": "ubuntu22"}), \
             mock.patch.object(build, "command", side_effect=execute):
            with self.assertRaises(subprocess.CalledProcessError):
                build.linux_packages("deb")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], "update")

    def test_environment_evidence_preserves_tool_records_without_exporting_secrets(self):
        output = self.root / "evidence.json"
        with mock.patch.dict(build.os.environ, {"GH_TOKEN": "do-not-export", "GITHUB_JOB": "fixture"}):
            build.write_evidence(output, aptSnapshot="fixed", systemPackages=["dpkg=1.2"])
            build.write_evidence(output, dotnet="8.0.427")
        evidence = json.loads(output.read_text())
        self.assertEqual(evidence["systemPackages"], ["dpkg=1.2"])
        self.assertEqual(evidence["dotnet"], "8.0.427")
        self.assertIn("config/build-environment.lock.json", evidence["lockSha256"])
        self.assertNotIn("do-not-export", output.read_text())

    def test_windows_tools_only_restore_reviewed_packages_into_a_private_sdk(self):
        archive = self.root / "verified-input.zip"
        archive.write_bytes(b"hash-verified fixture")
        lock = build.load_lock()
        versions = [lock["downloads"]["dotnet"]["version"], "7.0.0+fixture"]
        with mock.patch.object(build.sys, "platform", "win32"), mock.patch.object(build, "TOOLS", self.root / "tools"), \
             mock.patch.object(build, "fetch", return_value=archive), mock.patch.object(build, "extract"), \
             mock.patch.object(build.subprocess, "check_output", side_effect=versions), \
             mock.patch.object(build, "command") as commands, mock.patch.object(build, "write_evidence"), \
             mock.patch.dict(build.os.environ, {}, clear=True):
            build.windows_tools()
        install = commands.call_args_list[0]
        self.assertIn("--tool-path", install.args[0])
        self.assertNotIn("--global", install.args[0])
        config = self.root / "tools/nuget-feed/NuGet.Config"
        self.assertIn("<clear/>", config.read_text())
        self.assertNotIn("https:", config.read_text())
        self.assertEqual(install.kwargs["env"]["DOTNET_ROOT"], str(self.root / "tools/dotnet"))
        self.assertEqual(install.kwargs["env"]["DOTNET_MULTILEVEL_LOOKUP"], "0")

    def test_wrong_dotnet_version_cannot_proceed_to_tool_installation(self):
        archive = self.root / "verified-input.zip"
        archive.write_bytes(b"fixture")
        with mock.patch.object(build.sys, "platform", "win32"), mock.patch.object(build, "TOOLS", self.root / "tools"), \
             mock.patch.object(build, "fetch", return_value=archive), mock.patch.object(build, "extract"), \
             mock.patch.object(build.subprocess, "check_output", return_value="9.0.100"), \
             mock.patch.object(build, "command") as commands, self.assertRaisesRegex(ValueError, "SDK version mismatch"):
            build.windows_tools()
        commands.assert_not_called()


class WorkflowLockTests(unittest.TestCase):
    def test_current_workflows_and_dependency_locks_pass(self):
        policy.validate()

    def test_moving_action_python_node_and_removed_host_gate_are_rejected(self):
        for mutation in ("action", "python", "node", "host"):
            workflows = copy.deepcopy(load_workflows())
            steps = workflows["ci.yml"]["jobs"]["unit-and-websocket"]["steps"]
            if mutation == "action":
                steps[0]["uses"] = "actions/checkout@v4"
            elif mutation == "python":
                next(step for step in steps if "setup-python@" in step.get("uses", ""))["with"]["python-version"] = "3.12"
            elif mutation == "node":
                next(step for step in steps if "setup-node@" in step.get("uses", ""))["with"]["node-version"] = "22"
            else:
                steps[:] = [step for step in steps if step.get("name") != "Check locked build host"]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                policy.validate(workflows=workflows)

    def test_unhashed_and_partial_versions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "requirements.lock"
            for content in ("pip==26.2.2\n", "pip>=26 --hash=sha256:" + "a"*64, "pip==26.2.2 --hash=sha256:bad"):
                lock.write_text(content)
                with self.subTest(content=content), self.assertRaises(ValueError):
                    policy.requirements(lock)
