from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('static_console_builder', TOOLS / 'build-static-console.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class StaticConsolePackageTests(unittest.TestCase):
    def test_archive_has_complete_relative_assets_and_verified_manifest_only(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = builder.build(Path(directory) / 'console.zip')
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                self.assertEqual(len(names), 12)
                manifest = json.loads(bundle.read('manifest.json'))
                self.assertEqual(manifest['captureEntry'], 'capture/index.html')
                self.assertEqual(manifest['bSideEntry'], 'b-client-test/index.html')
                self.assertEqual({item['path'] for item in manifest['files']}, names - {'manifest.json'})
                for item in manifest['files']:
                    payload = bundle.read(item['path'])
                    self.assertEqual(item['sha256'], sha256(payload).hexdigest())
                    self.assertEqual(item['bytes'], len(payload))
                html = bundle.read('capture/index.html').decode()
                self.assertIn('src="./app.js"', html)
                self.assertIn('href="./styles.css"', html)
                self.assertIn('../b-client-test/index.html', html)
                self.assertIn(b'\r\n', bundle.read('b-client-test/configure-wired-ip.bat'))
