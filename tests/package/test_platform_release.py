from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from tools import platform_release as platform, publish_release as publication
from tools import release_pipeline as pipeline


class PlatformReleaseTests(unittest.TestCase):
    def inputs(self, root: Path, selected: str | None = None) -> None:
        commit = pipeline.run('git', 'rev-parse', 'HEAD')
        for target in pipeline.matrix():
            if selected and target['platform'] != selected:
                continue
            folder = root / target['id']
            folder.mkdir(parents=True)
            version = pipeline.platform_versions()[target['platform']]
            filename = f"neurobridge-{version}-{target['id']}.{target['format']}"
            package = folder / filename
            package.write_bytes(pipeline.MAGIC[target['format']] + b'fixture payload')
            (folder / 'validation.log').write_text('verified fixture\n')
            pipeline.save_json(folder / 'result.json', {
                'target': target, 'platformVersion': version, 'sourceCommit': commit,
                'status': 'candidate', 'reason': 'fixture', 'fileName': filename,
                'sha256': pipeline.sha256(package), 'validationLog': 'validation.log',
                'toolchain': 'fixture', 'runtimeSha256': '1' * 64, 'inputSha256': '2' * 64,
                'reportSha256': '3' * 64, 'builtAt': '2026-10-10T00:00:00Z',
                'sourceReferences': [{'kind': 'other', 'url': 'https://example.com/fixture',
                    'retrievedAt': '2026-10-10T00:00:00Z', 'sha256': '4' * 64}],
            })

    def release_info(self, archive: Path, name: str) -> dict:
        with zipfile.ZipFile(archive) as bundle:
            manifest = json.loads(bundle.read(platform.MANIFEST))
        checkpoint = {key: manifest[key] for key in ('platform', 'version', 'sourceCommit')}
        checkpoint.update(fileName=archive.name, sha256=pipeline.sha256(archive))
        return {'isDraft': False, 'url': 'https://example.com/release', 'assets': [{'name': archive.name}],
                'body': platform.CHECKPOINT + json.dumps(checkpoint) + '\n-->\n'}

    def test_kylin_release_does_not_require_windows_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.inputs(root / 'results', 'kylin')
            archive = platform.build_archive(root / 'results', root / 'release', 'kylin')
            manifest = platform.validate_archive(archive, 'kylin', pipeline.platform_versions()['kylin'])
            self.assertEqual(len(manifest['results']), 1)
            with self.assertRaises((ValueError, FileNotFoundError)):
                platform.build_archive(root / 'results', root / 'windows', 'windows')
            self.assertTrue(archive.is_file())

    def test_windows_requires_one_msi_and_one_exe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.inputs(root / 'results', 'windows')
            archive = platform.build_archive(root / 'results', root / 'release', 'windows')
            manifest = platform.validate_archive(archive, 'windows', pipeline.platform_versions()['windows'])
            self.assertEqual({r['target']['format'] for r in manifest['results']}, {'msi', 'exe'})
            self.assertEqual({r['target']['osVersion'] for r in manifest['results']}, {'10-11'})
            path = root / 'results/windows-10-11-x86_64-exe/result.json'
            result = json.loads(path.read_text());result['status'] = 'blocked';pipeline.save_json(path, result)
            with self.assertRaisesRegex(ValueError, 'requires all verified'):
                platform.build_archive(root / 'results', root / 'partial', 'windows')

    def test_fetch_verifies_checkpoint_bytes_and_tag_before_reusing_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.inputs(root / 'original', 'windows')
            archive = platform.build_archive(root / 'original', root / 'release', 'windows')
            info = self.release_info(archive, 'windows')
            commit = pipeline.run('git', 'rev-parse', 'HEAD')

            def command(*args):
                if args[:3] == ('gh', 'release', 'download'):
                    shutil.copy2(archive, Path(args[-1]) / archive.name)
                    return ''
                if args[:2] == ('git', 'rev-list'):
                    return commit
                if args[:2] == ('git', 'fetch'):
                    return ''
                self.fail(f'unexpected command {args}')

            with mock.patch.object(publication, 'release_info', return_value=info), mock.patch.object(publication, 'command', side_effect=command):
                platform.fetch_archive('windows', root / 'download', root / 'reused')
            result = json.loads((root / 'reused/windows-10-11-x86_64-msi/result.json').read_text())
            self.assertEqual(result['sourceCommit'], commit)
            self.assertEqual(result['reusedRelease']['archiveSha256'], pipeline.sha256(archive))
            self.assertEqual(result['platformVersion'], pipeline.platform_versions()['windows'])

            for failure in ('draft', 'wrong-checkpoint', 'corrupted-download', 'wrong-tag'):
                bad_info = json.loads(json.dumps(info))
                if failure == 'draft':bad_info['isDraft'] = True
                if failure == 'wrong-checkpoint':bad_info['body'] = bad_info['body'].replace(pipeline.sha256(archive), '0' * 64)
                def bad_command(*args):
                    value = command(*args)
                    if failure == 'corrupted-download' and args[:3] == ('gh', 'release', 'download'):
                        (Path(args[-1]) / archive.name).write_bytes(b'corrupt')
                    if failure == 'wrong-tag' and args[:2] == ('git', 'rev-list'):return 'a' * 40
                    return value
                output = root / failure
                with self.subTest(failure=failure), mock.patch.object(publication, 'release_info', return_value=bad_info), mock.patch.object(publication, 'command', side_effect=bad_command), self.assertRaises(ValueError):
                    platform.fetch_archive('windows', root / 'download', output)
                self.assertFalse(output.exists())

    def test_uploaded_platform_digest_is_checked_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.inputs(root / 'results', 'kylin')
            archive = platform.build_archive(root / 'results', root / 'release', 'kylin')
            with mock.patch.object(publication, 'ensure_tag') as tag, mock.patch.object(publication, 'ensure_release') as release, self.assertRaisesRegex(ValueError, 'Actions Artifact differs'):
                platform.publish_platform(archive, 'kylin', '0' * 64)
            tag.assert_not_called();release.assert_not_called()

    def test_platform_publication_verifies_bytes_before_making_release_public(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.inputs(root / 'results', 'kylin')
            archive = platform.build_archive(root / 'results', root / 'release', 'kylin')
            info = self.release_info(archive, 'kylin')
            commit = pipeline.run('git', 'rev-parse', 'HEAD')
            events = []

            def command(*args):
                if args == ('git', 'rev-parse', 'HEAD'):
                    return commit
                if args == ('git', 'status', '--porcelain', '--untracked-files=no'):
                    return ''
                if args[:3] == ('gh', 'release', 'edit'):
                    self.assertIn('--draft=false', args)
                    events.append('public')
                    return ''
                self.fail(f'unexpected command {args}')

            for verification_failed in (False, True):
                events.clear()
                def verify(*args):
                    events.append('verify')
                    if verification_failed:
                        raise ValueError('downloaded asset differs')
                with mock.patch.object(publication, 'command', side_effect=command), \
                        mock.patch.object(publication, 'ensure_tag'), \
                        mock.patch.object(publication, 'ensure_release', return_value={'isDraft': True, 'assets': []}), \
                        mock.patch.object(publication, 'verify_asset', side_effect=verify), \
                        mock.patch.object(publication, 'release_info', return_value=info):
                    if verification_failed:
                        with self.assertRaisesRegex(ValueError, 'asset differs'):
                            platform.publish_platform(archive, 'kylin', pipeline.sha256(archive))
                        self.assertEqual(events, ['verify'])
                    else:
                        receipt = platform.publish_platform(archive, 'kylin', pipeline.sha256(archive))
                        self.assertEqual(events, ['verify', 'public'])
                        self.assertEqual(receipt['version'], pipeline.platform_versions()['kylin'])
                        self.assertEqual(receipt['sourceCommit'], commit)

    def test_retry_restores_original_bytes_without_reuse_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.inputs(root / 'original', 'kylin')
            archive = platform.build_archive(root / 'original', root / 'release', 'kylin')
            info = self.release_info(archive, 'kylin')
            info['isDraft'] = True
            commit = pipeline.run('git', 'rev-parse', 'HEAD')
            def command(*args):
                if args[:3] == ('gh', 'release', 'download'):
                    shutil.copy2(archive, Path(args[-1]) / archive.name)
                    return ''
                if args[:2] == ('git', 'rev-list'):
                    return commit
                if args[:2] == ('git', 'fetch'):
                    return ''
                self.fail(f'unexpected command {args}')
            with mock.patch.object(publication, 'release_info', return_value=info), \
                    mock.patch.object(publication, 'command', side_effect=command):
                restored = platform.fetch_archive('kylin', root / 'recovered', root / 'results', retry=True)
            self.assertEqual(restored.read_bytes(), archive.read_bytes())
            result_path = root / 'results' / next(t['id'] for t in pipeline.matrix() if t['platform'] == 'kylin') / 'result.json'
            self.assertNotIn('reusedRelease', json.loads(result_path.read_text()))

    def test_tampered_archive_paths_identity_and_payload_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.inputs(root / 'results', 'kylin')
            archive = platform.build_archive(root / 'results', root / 'release', 'kylin')
            for failure in ('path', 'version', 'commit', 'extra', 'payload'):
                broken = root / (failure + '.zip')
                with zipfile.ZipFile(archive) as source:
                    entries = {name: source.read(name) for name in source.namelist()}
                manifest = json.loads(entries[platform.MANIFEST])
                if failure == 'path':manifest['results'][0]['fileName'] = '../outside.deb'
                if failure == 'version':manifest['version'] = '99.0.0'
                if failure == 'commit':manifest['results'][0]['sourceCommit'] = 'a' * 40
                if failure == 'extra':entries['../outside'] = b'bad'
                if failure == 'payload':
                    name = next(name for name in entries if name.endswith('.deb'));entries[name] = b'bad'
                entries[platform.MANIFEST] = json.dumps(manifest).encode()
                with zipfile.ZipFile(broken, 'w') as target:
                    for name, value in entries.items():target.writestr(name, value)
                with self.subTest(failure=failure), self.assertRaises(ValueError):
                    platform.validate_archive(broken, 'kylin', pipeline.platform_versions()['kylin'])


if __name__ == '__main__':
    unittest.main()
