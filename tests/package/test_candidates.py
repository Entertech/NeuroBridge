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
                self.assertFalse(manifest["signed"])
                self.assertFalse(manifest["runtimeBundled"])
                self.assertTrue(any(name.endswith("metadata/sbom.cdx.json") for name in names))
                self.assertFalse(any("/.git/" in name or "__pycache__" in name or name.endswith(".pyc") for name in names))

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
