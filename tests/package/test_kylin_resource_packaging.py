"""Verify resource omission, platform variants and actual archive integrity."""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from tools.kylin_inputs import catalog, offline_manifest, selected_artifacts, selection_key
from tools.kylin_package import PAYLOAD, REQUIRED, inspect_payload
from tests.package.test_kylin_bootstrap import BUILDER, ROOT


class ResourcePackagingTests(unittest.TestCase):
    def test_resource_selection_keeps_both_architecture_variants(self):
        data = catalog()
        self.assertEqual(len(selected_artifacts(data)), 7)
        self.assertEqual(set(selected_artifacts(data, 'python,cmake')),
                         {'python_x86_64', 'python_source', 'cmake_x86_64', 'cmake_source'})
        self.assertEqual(selected_artifacts(data, 'none'), {})
        self.assertEqual(selection_key('eigen, python'), selection_key('python,eigen'))
        for value in ('', 'all,cmake', 'cmake,cmake', 'python,', 'typo', 'none,python'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                selection_key(value)

    def test_none_preparation_never_downloads_or_needs_a_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'missing'
            result = subprocess.run([sys.executable, str(ROOT / 'tools/prepare-kylin-bootstrap-inputs.py'),
                                     '--offline-resources', 'none', '--download', '--cache-dir', str(cache)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(cache.exists())
            self.assertNotIn('download_start', result.stdout)

    def test_payload_only_reads_and_ships_selected_inputs(self):
        for selection in ('none', 'pyserial', 'python,cmake', 'all'):
            with self.subTest(selection=selection), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                data = catalog()
                selected = selected_artifacts(data, selection)
                fixture = root / 'fixture'
                fixture.write_bytes(b'input fixture')
                with mock.patch.object(BUILDER, 'verified_input', return_value=fixture) as verified:
                    BUILDER.stage_payload(root / 'stage', selection)
                self.assertEqual([call.args[0]['filename'] for call in verified.call_args_list],
                                 [item['filename'] for item in selected.values()])
                payload = root / 'stage' / PAYLOAD
                self.assertEqual(json.loads((payload / 'offline-resources.json').read_text()),
                                 offline_manifest(data, selection))
                expected = {payload / item['path'] for item in offline_manifest(data, selection)['artifacts']}
                actual = {path for directory in ('python-runtime', 'wheelhouse', 'packaging/kylin/offline')
                          for path in (payload / 'source' / directory).rglob('*') if path.is_file()}
                self.assertEqual(actual, expected)

    def fixture(self, selection):
        data = copy.deepcopy(catalog())
        content = b'fixture locked artifact'
        for item in data['artifacts'].values():
            item['sha256'] = hashlib.sha256(content).hexdigest()
        manifest = offline_manifest(data, selection)
        files = {name: b'fixture script' for name in REQUIRED}
        files['offline-resources.json'] = json.dumps(manifest).encode()
        files['kylin-bootstrap-inputs.toml'] = b'fixture lock'
        files.update({item['path']: content for item in manifest['artifacts']})
        return data, files

    def test_repository_local_offline_files_cannot_leak_into_none_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / 'repository'
            for relative in BUILDER.SOURCE_DIRS:
                (repository / relative).mkdir(parents=True, exist_ok=True)
            files = set(BUILDER.SOURCE_FILES) | {
                'config/kylin-bootstrap-inputs.toml', 'packaging/kylin/run-logged.sh',
                'packaging/kylin/export-install-logs.sh', 'packaging/kylin/diagnostic-context.sh',
                'packaging/kylin/platform.sh', 'packaging/kylin/resources.sh',
                'packaging/kylin/install-bootstrap.sh', 'packaging/kylin/70-neurobridge-usb-serial.rules',
                'packaging/kylin/offline/unwanted.tar.gz',
                'packaging/kylin/offline/wheelhouse/unwanted.whl',
            }
            for relative in files:
                path = repository / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'repository fixture')
            with mock.patch.object(BUILDER, 'ROOT', repository), \
                    mock.patch.object(BUILDER.subprocess, 'check_output', return_value='a' * 40), \
                    mock.patch.object(BUILDER, 'verified_input', side_effect=AssertionError('none needs no inputs')):
                BUILDER.stage_payload(root / 'stage', 'none')
            payload = root / 'stage' / PAYLOAD
            self.assertFalse(list((payload / 'source/packaging/kylin/offline').rglob('*')))

    def inspect(self, files, data, selection):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            for name, content in files.items():
                member = tarfile.TarInfo('./' + PAYLOAD + name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode='r|') as archive:
            return inspect_payload(archive, selection, data, b'fixture lock')

    def test_actual_payload_verification_handles_all_none_and_partial(self):
        digests = set()
        for selection, count in (('all', 7), ('none', 0), ('python,cmake', 4)):
            data, files = self.fixture(selection)
            result = self.inspect(files, data, selection)
            self.assertEqual(result['artifactCount'], count)
            self.assertEqual(result['resourceBytes'], count * len(b'fixture locked artifact'))
            digests.add(result['inputSha256'])
        self.assertEqual(len(digests), 3)

    def test_actual_payload_rejects_missing_wrong_and_unselected_files(self):
        for failure in ('missing', 'checksum', 'unselected', 'manifest', 'lock'):
            with self.subTest(failure=failure):
                data, files = self.fixture('pyserial')
                resource = offline_manifest(data, 'pyserial')['artifacts'][0]['path']
                if failure == 'missing':
                    del files[resource]
                elif failure == 'checksum':
                    files[resource] = b'corrupt archive'
                elif failure == 'unselected':
                    files['source/packaging/kylin/offline/unselected.tar.gz'] = b'unwanted'
                elif failure == 'manifest':
                    files['offline-resources.json'] = json.dumps(offline_manifest(data, 'all')).encode()
                else:
                    files['kylin-bootstrap-inputs.toml'] = b'wrong lock'
                with self.assertRaises(ValueError):
                    self.inspect(files, data, 'pyserial')


if __name__ == '__main__':
    unittest.main()
