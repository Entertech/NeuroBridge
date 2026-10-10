"""Reproduce implicit tar directory modes under the install logger's umask."""

import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipIf(os.name == "nt", "Kylin uses POSIX permissions")
class RuntimePermissionsTests(unittest.TestCase):
    def test_sparse_archive_becomes_service_readable_without_following_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "python.tar"
            with tarfile.open(archive, "w") as output:
                for name, mode in (("bin/python3", 0o775), ("lib/encodings/__init__.py", 0o600)):
                    body = b"fixture\n"
                    entry = tarfile.TarInfo(name)
                    entry.mode, entry.size = mode, len(body)
                    output.addfile(entry, io.BytesIO(body))
            runtime = root / "runtime"
            runtime.mkdir()
            result = subprocess.run(["bash", "-c", 'umask 077; tar -xf "$1" -C "$2"',
                                     "fixture", str(archive), str(runtime)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((runtime / "bin").stat().st_mode & 0o777, 0o700)
            private = root / "recordings"
            private.mkdir(mode=0o700)
            secret = private / "data"
            secret.write_text("private")
            secret.chmod(0o600)
            (runtime / "outside").symlink_to(private, target_is_directory=True)
            (runtime / "python").symlink_to("bin/python3")
            result = subprocess.run(["bash", "-c", 'source "$1"; nb_normalize_runtime_permissions "$2"',
                                     "fixture", str(ROOT / "packaging/kylin/platform.sh"), str(runtime)],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for path in (runtime, runtime / "bin", runtime / "lib", runtime / "lib/encodings"):
                self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            self.assertEqual((runtime / "python").stat().st_mode & 0o777, 0o755)
            self.assertEqual((runtime / "lib/encodings/__init__.py").stat().st_mode & 0o777, 0o644)
            self.assertTrue((runtime / "outside").is_symlink())
            self.assertEqual(private.stat().st_mode & 0o777, 0o700)
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
            self.assertIn("phase=runtime_permissions", result.stdout + result.stderr)
