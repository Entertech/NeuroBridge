from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "tools" / "verify-offline-algorithm-bundle.py"


def write_archive(path: Path, directory: str) -> str:
    with tarfile.open(path, "w") as archive:
        content = b"offline source"
        item = tarfile.TarInfo(f"{directory}/source-marker")
        item.size = len(content)
        archive.addfile(item, BytesIO(content))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OfflineBundleTests(unittest.TestCase):
    def test_verifier_accepts_matching_ubuntu24_source_archives(self) -> None:
        lock = tomllib.loads((ROOT / "sdk.lock").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, destination = root / "bundle", root / "sdk"
            (bundle / "sdk").mkdir(parents=True)
            affective_hash = write_archive(bundle / "sdk" / "affective.tar", "AffectiveCloud-Algorithm-SDK")
            numcpp_hash = write_archive(bundle / "sdk" / "numcpp.tar", "NumCpp")
            bundle.joinpath("manifest.toml").write_text(
                "\n".join(
                    [
                        "[bundle]",
                        "format_version = 1",
                        'ubuntu_version = "24.04"',
                        'architecture = "amd64"',
                        "",
                        "[archives.affective]",
                        'path = "sdk/affective.tar"',
                        f'commit = "{lock["affective_algorithm_sdk"]["commit"]}"',
                        f'sha256 = "{affective_hash}"',
                        "",
                        "[archives.numcpp]",
                        'path = "sdk/numcpp.tar"',
                        f'commit = "{lock["affective_algorithm_sdk"]["build"]["numcpp_commit"]}"',
                        f'sha256 = "{numcpp_hash}"',
                    ]
                ),
                encoding="utf-8",
            )
            subprocess.run([sys.executable, str(VERIFY), str(bundle), str(destination)], check=True, capture_output=True, text=True)
            self.assertTrue((destination / "AffectiveCloud-Algorithm-SDK" / "source-marker").is_file())
            self.assertTrue((destination / "NumCpp" / "source-marker").is_file())

    def test_verifier_rejects_a_checksum_mismatch(self) -> None:
        lock = tomllib.loads((ROOT / "sdk.lock").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, destination = root / "bundle", root / "sdk"
            (bundle / "sdk").mkdir(parents=True)
            write_archive(bundle / "sdk" / "affective.tar", "AffectiveCloud-Algorithm-SDK")
            numcpp_hash = write_archive(bundle / "sdk" / "numcpp.tar", "NumCpp")
            bundle.joinpath("manifest.toml").write_text(
                "\n".join(
                    [
                        "[bundle]",
                        "format_version = 1",
                        'ubuntu_version = "24.04"',
                        'architecture = "amd64"',
                        "",
                        "[archives.affective]",
                        'path = "sdk/affective.tar"',
                        f'commit = "{lock["affective_algorithm_sdk"]["commit"]}"',
                        'sha256 = "not-a-valid-checksum"',
                        "",
                        "[archives.numcpp]",
                        'path = "sdk/numcpp.tar"',
                        f'commit = "{lock["affective_algorithm_sdk"]["build"]["numcpp_commit"]}"',
                        f'sha256 = "{numcpp_hash}"',
                    ]
                ),
                encoding="utf-8",
            )
            result = subprocess.run([sys.executable, str(VERIFY), str(bundle), str(destination)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("checksum", result.stderr)
