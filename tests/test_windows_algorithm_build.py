from hashlib import sha256
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from windows import algorithm_build as builder


class WindowsAlgorithmBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_offline_and_hash_mismatch_never_download(self):
        package = {'filename': 'tool.zip', 'sha256': sha256(b'correct').hexdigest(), 'url': 'https://invalid.test/tool.zip'}
        with patch.object(builder.urllib.request, 'urlopen') as download:
            with self.assertRaisesRegex(ValueError, 'Offline build input missing'):
                builder.obtain_archive(package, self.root, True)
            (self.root / 'tool.zip').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                builder.obtain_archive(package, self.root, False)
            download.assert_not_called()

    def test_reject_unsafe_archives_before_extracting(self):
        for name in ('../escape', 'C:/escape', '/escape', 'folder/../../escape', 'file:stream'):
            archive = self.root / 'unsafe.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr(name, 'unsafe')
            with self.assertRaisesRegex(ValueError, 'Unsafe ZIP'):
                builder.extract_archive(archive, self.root / 'unpacked')
        self.assertFalse((self.root / 'unpacked').exists())

    def test_reject_wrong_executable_architecture_and_runtime_dlls(self):
        path = self.root / 'bridge.exe'
        header = bytearray(64)
        header[:2] = b'MZ'
        struct.pack_into('<I', header, 60, 64)
        path.write_bytes(header + b'PE\0\0\x64\x86')
        builder.validate_pe(path)
        path.write_bytes(header + b'PE\0\0\x4c\x01')
        with self.assertRaisesRegex(ValueError, 'AMD64'):
            builder.validate_pe(path)
        builder.validate_imports('  Name: KERNEL32.dll\n  Name: api-ms-win-crt-runtime-l1-1-0.dll\n')
        with self.assertRaisesRegex(ValueError, 'Non-system'):
            builder.validate_imports('  Name: libc++.dll\n')

    def test_failed_manifest_install_restores_old_binary(self):
        candidate = self.root / 'candidate.exe'
        candidate.write_bytes(b'new')
        destination = self.root / 'installed/bridge.exe'
        destination.parent.mkdir()
        destination.write_bytes(b'old')
        manifest_path = destination.with_suffix('.manifest.json')
        manifest_path.write_text('{"old":true}')
        replace = os.replace

        def fail_manifest(source, target):
            if Path(target) == manifest_path:
                raise PermissionError('manifest locked')
            return replace(source, target)

        with patch.object(builder.os, 'replace', side_effect=fail_manifest):
            with self.assertRaises(PermissionError):
                builder.install_verified(candidate, {'new': True}, destination)
        self.assertEqual(destination.read_bytes(), b'old')
        self.assertEqual(json.loads(manifest_path.read_text()), {'old': True})

    def test_source_fingerprint_changes_with_native_source(self):
        for relative in ('sdk.lock', 'windows/algorithm_build.py', 'mac/algorithm_bridge/bridge.cpp',
                         'third_party/AffectiveCloud-Algorithm-SDK/sdk.cpp', 'third_party/NumCpp/include.hpp'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('original')
        initial = builder.source_fingerprint(self.root)
        (self.root / 'mac/algorithm_bridge/bridge.cpp').write_text('changed')
        self.assertNotEqual(initial, builder.source_fingerprint(self.root))

    def test_matching_manifest_reuses_binary_without_downloading(self):
        from types import SimpleNamespace
        destination = self.root / '.runtime/algorithm/neurobridge_affective_bridge.exe'
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b'verified fixture')
        destination.with_suffix('.manifest.json').write_text(json.dumps({
            'sourceFingerprint': 'same-source', 'sha256': builder.digest(destination)}))
        config = SimpleNamespace(algorithm=SimpleNamespace(command=(str(destination),)))
        with patch.object(builder, 'validate_config', return_value=config), \
             patch.object(builder, 'source_fingerprint', return_value='same-source'), \
             patch.object(builder, 'smoke', return_value={}) as smoke, \
             patch.object(builder, 'obtain_archive') as download:
            self.assertEqual(builder.build(self.root, offline=True), destination)
            smoke.assert_called_once_with(destination)
            download.assert_not_called()

    def test_custom_algorithm_path_is_never_overwritten(self):
        from types import SimpleNamespace
        external = self.root / 'custom.exe'
        external.write_bytes(b'operator managed')
        config = SimpleNamespace(algorithm=SimpleNamespace(command=(str(external),)))
        with patch.object(builder, 'validate_config', return_value=config), \
             patch.object(builder, 'smoke', return_value={}), \
             patch.object(builder, 'obtain_archive') as download:
            self.assertEqual(builder.build(self.root), external)
            self.assertEqual(external.read_bytes(), b'operator managed')
            download.assert_not_called()

    def test_smoke_preserves_windows_case_insensitive_environment_semantics(self):
        for root_key, path_key in [('SYSTEMROOT', 'PATH'), ('SystemRoot', 'Path')]:
            with self.subTest(root_key=root_key), \
                 patch.dict(builder.os.environ, {root_key: str(self.root), path_key: 'compiler-dll-directory'}, clear=True), \
                 patch.object(builder, 'validate_pe'), \
                 patch.object(builder, 'smoke_test_bridge', return_value={}) as smoke:
                builder.smoke(self.root / 'candidate.exe')
                env = smoke.call_args.kwargs['environment']
                self.assertEqual(env['SYSTEMROOT'], str(self.root))
                self.assertEqual(env['PATH'], os.pathsep.join(map(str, (self.root / 'System32', self.root))))
                self.assertNotIn('compiler-dll-directory', env['PATH'])
                self.assertNotIn('Path', env)

    def test_missing_system_root_fails_with_actionable_error(self):
        with patch.dict(builder.os.environ, {}, clear=True), \
             patch.object(builder, 'validate_pe'), \
             patch.object(builder, 'smoke_test_bridge') as smoke:
            with self.assertRaisesRegex(ValueError, 'SYSTEMROOT environment variable is missing'):
                builder.smoke(self.root / 'candidate.exe')
            smoke.assert_not_called()


if __name__ == '__main__':
    unittest.main()
