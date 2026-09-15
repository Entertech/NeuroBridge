from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]


class CandidatePackageTests(unittest.TestCase):
    def test_unsigned_candidates_have_manifest_sbom_checksum_and_clean_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            for platform_name in ("kylin", "windows"):
                subprocess.run(
                    [sys.executable, str(ROOT / "tools/build-product-candidate.py"), "--platform", platform_name, "--output-dir", str(output)],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            archives = sorted(path for path in output.iterdir() if path.suffix in {".gz", ".zip"})
            self.assertEqual(len(archives), 2)
            for archive in archives:
                self.assertTrue(Path(str(archive) + ".sha256").is_file())
                if archive.suffix == ".zip":
                    with zipfile.ZipFile(archive) as bundle:
                        names = bundle.namelist()
                        manifest_name = next(name for name in names if name.endswith("metadata/manifest.json"))
                        manifest = json.loads(bundle.read(manifest_name))
                else:
                    with tarfile.open(archive) as bundle:
                        names = bundle.getnames()
                        manifest_name = next(name for name in names if name.endswith("metadata/manifest.json"))
                        member = bundle.extractfile(manifest_name)
                        assert member is not None
                        manifest = json.load(member)
                self.assertTrue(any(name.endswith("metadata/files.sha256") for name in names))
                self.assertTrue(any(name.endswith("payload/defaults.toml") for name in names))
                release = json.loads(Path(str(archive) + ".release-manifest.json").read_text())
                self.assertEqual(release["packageSize"], archive.stat().st_size)
                self.assertFalse(release["licenseTextsComplete"])
                self.assertFalse(manifest["signed"])
                self.assertFalse(manifest["runtimeBundled"])
                self.assertFalse(manifest["targetAcceptancePassed"])
                self.assertIn("sourceDirty", manifest)
                self.assertIn("payload/neurobridge/version_registry.toml", manifest["files"])
                self.assertTrue(any(name.endswith("metadata/sbom.cdx.json") for name in names))
                self.assertFalse(any("/.git/" in name or "__pycache__" in name or name.endswith(".pyc") for name in names))

    def test_candidate_checksums_detect_a_modified_payload(self):
        import shutil
        command = ([shutil.which("sha256sum"), "--check"] if shutil.which("sha256sum") else
                   [shutil.which("shasum"), "-a", "256", "--check"] if shutil.which("shasum") else None)
        if command is None:
            self.skipTest("Platform checksum CLI is not available")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run([sys.executable, str(ROOT / "tools/build-product-candidate.py"),
                            "--platform", "kylin", "--output-dir", str(root)], check=True, capture_output=True)
            with tarfile.open(next(root.glob("*.tar.gz"))) as bundle:
                bundle.extractall(root / "unpacked", filter="data")
            stage = next((root / "unpacked").iterdir())
            verified = subprocess.run([*command, "metadata/files.sha256"], cwd=stage, capture_output=True)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            (stage / "payload/neurobridge/__init__.py").write_text("modified fixture")
            rejected = subprocess.run([*command, "metadata/files.sha256"], cwd=stage, capture_output=True)
            self.assertNotEqual(rejected.returncode, 0)


    def test_runtime_layout_is_validated_before_packaging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools/build-product-candidate.py"),
                    "--platform",
                    "windows",
                    "--output-dir",
                    str(root / "output"),
                    "--runtime-dir",
                    str(runtime),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("python.exe", completed.stderr)
            self.assertIn("neurobridge_affective_bridge.exe", completed.stderr)

    def test_windows_candidate_uses_one_consistent_runtime_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "python.exe").write_bytes(b"runtime")
            (runtime / "neurobridge_affective_bridge.exe").write_bytes(b"bridge")
            output = root / "output"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools/build-product-candidate.py"),
                    "--platform",
                    "windows",
                    "--output-dir",
                    str(output),
                    "--runtime-dir",
                    str(runtime),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            archive = next(output.glob("*.zip"))
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                manifest_name = next(name for name in names if name.endswith("metadata/manifest.json"))
                manifest = json.loads(bundle.read(manifest_name))
                install_name = next(name for name in names if name.endswith("install.ps1"))
                config_name = next(name for name in names if name.endswith("gateway.toml.example"))
                install = bundle.read(install_name).decode("utf-8")
                config = tomllib.loads(bundle.read(config_name).decode("utf-8"))
            self.assertTrue(manifest["runtimeBundled"])
            self.assertTrue(any(name.endswith("payload/runtime/python.exe") for name in names))
            self.assertIn('runtime\\python.exe', install)
            self.assertTrue(config["algorithm"]["command"][0].endswith(r"runtime\neurobridge_affective_bridge.exe"))

    def test_kylin_rollback_restores_all_state_and_retains_replaced_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "opt/neurobridge-rollback.ABC123"
            for path in (backup / "app", root / "opt/neurobridge", root / "etc/neurobridge", root / "etc/systemd/system"):
                path.mkdir(parents=True)
            (backup / "app/version").write_text("old")
            (root / "opt/neurobridge/version").write_text("new")
            (backup / "gateway.toml").write_text("old config")
            (backup / "neurobridge.service").write_text("old unit")
            (root / "etc/neurobridge/gateway.toml").write_text("new config")
            (root / "etc/systemd/system/neurobridge.service").write_text("new unit")
            for flag in ("had-app", "was-active", "was-enabled"):
                (backup / flag).touch()
            script = (ROOT / "packaging/kylin/rollback.sh").read_text()
            # Execute only against this disposable fixture with service calls
            # mocked; never run the system installer on the development host.
            script = script.replace('[[ ${EUID} -eq 0 ]]', 'true')
            script = script.replace('/opt/', str(root) + '/opt/').replace('/etc/', str(root) + '/etc/')
            completed = subprocess.run(["bash", "-c", 'systemctl() { return 0; };\n' + script, "rollback-fixture", str(backup)],
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual((root / "opt/neurobridge/version").read_text(), "old")
            self.assertEqual((root / "etc/neurobridge/gateway.toml").read_text(), "old config")
            self.assertEqual((root / "etc/systemd/system/neurobridge.service").read_text(), "old unit")
            self.assertEqual((backup / "failed-app/version").read_text(), "new")
            self.assertTrue((backup / "restored").is_file())
