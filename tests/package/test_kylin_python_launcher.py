"""Exercise Python prefix discovery after deployment-directory relocation."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipIf(os.name == "nt", "Kylin launcher uses a POSIX shell")
class KylinPythonLauncherTests(unittest.TestCase):
    def test_relocated_runtime_imports_its_own_encodings_with_stale_pythonhome(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "build tree"
            prefix = original / "runtime/bin/python-runtime"
            (prefix / "bin").mkdir(parents=True)
            # Use the host interpreter only to exercise prefix discovery. This
            # is not evidence of a Kylin target build or physical acceptance.
            (prefix / "bin/python3").symlink_to(sys.executable)
            library = prefix / f"lib/python{sys.version_info.major}.{sys.version_info.minor}"
            shutil.copytree(Path(os.__file__).parent, library,
                            ignore=shutil.ignore_patterns("__pycache__", "site-packages", "test", "tests"))
            entry = original / "runtime/bin/python"
            shutil.copy2(ROOT / "packaging/kylin/python-launcher.sh", entry)
            entry.chmod(0o755)
            relocated = original.with_name("installed tree")
            original.rename(relocated)
            entry = relocated / "runtime/bin/python"
            prefix = relocated / "runtime/bin/python-runtime"
            env = {**os.environ, "PYTHONHOME": str(original / "missing"), "PYTHONPATH": ""}
            broken = subprocess.run(
                [sys.executable, "-S", "-c", "import encodings"], env=env,
                text=True, capture_output=True,
            )
            self.assertNotEqual(broken.returncode, 0)
            self.assertIn("encodings", broken.stderr)
            result = subprocess.run(
                [str(entry), "-S", "-c",
                 "import encodings,sys; print(sys.prefix); print(encodings.__file__); print(sys.argv[1])",
                 "argument with spaces"], cwd=directory,
                env=env,
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            self.assertEqual(Path(lines[0]), prefix.resolve())
            self.assertEqual(Path(lines[1]).parent.name, "encodings")
            self.assertTrue(Path(lines[1]).resolve().is_relative_to(prefix.resolve()))
            self.assertEqual(lines[2], "argument with spaces")


if __name__ == "__main__":
    unittest.main()
