from pathlib import Path
import shutil
import subprocess
import unittest


class BClientPageTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the static-page runtime test")
    def test_opening_the_page_connects_to_deployment_endpoint_and_handles_reconnection(self):
        subprocess.run(["node", str(Path(__file__).with_name("b_client_page.cjs"))], check=True, capture_output=True, text=True, timeout=10)
