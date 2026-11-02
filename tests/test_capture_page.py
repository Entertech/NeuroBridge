from pathlib import Path
import shutil
import subprocess
import unittest


class CapturePageTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for the static capture-page runtime check')
    def test_file_and_hosted_capture_pages_control_the_gateway(self):
        subprocess.run(['node', str(Path(__file__).with_name('capture_page.cjs'))], check=True, capture_output=True, text=True, timeout=10)
