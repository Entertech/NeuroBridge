"""Real filesystem rotation, Windows fsync semantics, and failure isolation."""
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from neurobridge.adapters.storage.filesystem import SegmentedRecordingRepository
from neurobridge.ports.recording import PersistenceRecord


class RecordingRotationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo = self.make_repo()
        self.started = int(time.time() * 1000)

    def make_repo(self, **kwargs):
        repo = SegmentedRecordingRepository(self.root, warning_threshold_bytes=100,
            critical_threshold_bytes=10, recovery_margin_bytes=1, **kwargs)
        self.addAsyncCleanup(repo.close)
        return repo

    def record(self, kind, timestamp, identifier):
        return PersistenceRecord(1, 'rec-test', kind, timestamp, identifier, {'synthetic': True})

    async def append(self, kind, timestamp, identifier):
        return await self.repo.confirm(self.repo.try_append(self.record(kind, timestamp, identifier)))

    def manifest(self):
        return json.loads((self.root / 'sessions/rec-test/manifest.json').read_text(encoding='utf-8'))

    def check_indexed_rows(self):
        identifiers = []
        for entry in self.manifest()['segments']:
            data = (self.root / 'sessions/rec-test' / entry['path']).read_bytes()
            self.assertEqual(sha256(data).hexdigest(), entry['sha256'])
            self.assertEqual(len(data), entry['byteLength'])
            rows = [json.loads(line) for line in data.splitlines()]
            self.assertEqual(len(rows), entry['recordCount'])
            identifiers.extend(row['correlationId'] for row in rows)
        return identifiers

    async def test_repeated_ten_minute_rotations_and_close_with_windows_fsync_rules(self):
        real_fsync = os.fsync

        def writable_fsync(fd):
            # POSIX accepts read-only fsync; Windows _commit rejects it. Native
            # Windows CI exercises the OS directly; local CI emulates the rule.
            if os.name != 'nt':
                import fcntl
                if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY:
                    raise OSError(errno.EBADF, 'Windows requires a writable fsync descriptor')
            real_fsync(fd)

        expected = []
        with patch('neurobridge.adapters.storage.filesystem.os.fsync', writable_fsync):
            for window in range(4):
                for kind in ('raw.device_frame', 'parsed.signal_batch', 'algorithm.result'):
                    identifier = f'{kind}-{window}'
                    expected.append(identifier)
                    result = await self.append(kind, self.started + window * 601000, identifier)
                    self.assertTrue(result.persistence_guaranteed, result.reason)
            await self.repo.close_session('rec-test')
        self.assertCountEqual(self.check_indexed_rows(), expected)
        self.assertEqual(len(self.manifest()['segments']), 12)
        self.assertIn('endedAtMs', self.manifest())
        self.assertEqual(self.repo.storage_status().gap_count, 0)
        self.assertFalse(list(self.root.rglob('*.partial')))
        self.assertFalse(list(self.root.rglob('*.tmp')))

    async def test_size_rotation_keeps_every_confirmed_record(self):
        self.repo.segment_max_bytes = 1
        for i in range(3):
            result = await self.append('raw.device_frame', self.started, str(i))
            self.assertTrue(result.persistence_guaranteed)
        await self.repo.close_session('rec-test')
        self.assertCountEqual(self.check_indexed_rows(), ['0', '1', '2'])
        self.assertEqual(len(self.manifest()['segments']), 3)

    async def test_rotation_sync_failure_does_not_acknowledge_earlier_buffered_rows(self):
        await self.repo.close()
        # Queue one deterministic batch before allowing the writer to start.
        with patch('neurobridge.adapters.storage.filesystem.Thread.start'):
            self.repo = self.make_repo()
        receipts = [self.repo.try_append(row) for row in (
            self.record('raw.device_frame', self.started, 'buffered-raw'),
            self.record('raw.device_frame', self.started + 601000, 'rotate-raw'),
            self.record('parsed.signal_batch', self.started, 'unrelated-parsed'),
        )]
        real_fsync = os.fsync
        failed = False

        def fail_first_sync(fd):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError(errno.EIO, 'synthetic segment sync failure')
            real_fsync(fd)

        with patch('neurobridge.adapters.storage.filesystem.os.fsync', fail_first_sync):
            self.repo._worker.start()
            results = [await self.repo.confirm(receipt) for receipt in receipts]
        self.assertEqual([result.persistence_guaranteed for result in results], [False, False, True])
        self.assertEqual(self.repo.storage_status().gap_count, 2)

    async def check_rotation_failure(self, failure_stage):
        await self.append('raw.device_frame', self.started, 'raw-1')
        await self.append('parsed.signal_batch', self.started, 'parsed-1')
        # Commit an existing index first, so failure must preserve it verbatim.
        await self.append('raw.device_frame', self.started + 601000, 'raw-2')
        manifest_path = self.root / 'sessions/rec-test/manifest.json'
        previous = manifest_path.read_bytes()
        real_replace = os.replace
        real_update = self.repo._update_manifest
        failed = False

        def fail_once(source, destination):
            nonlocal failed
            source, destination = Path(source), Path(destination)
            target = (destination.name == 'manifest.json' if failure_stage == 'manifest'
                      else source.name.endswith('.partial') and source.parent.name == 'raw')
            if target and not failed:
                failed = True
                raise OSError(errno.EACCES, 'synthetic rename failure')
            return real_replace(source, destination)

        def fail_sync_once(session, entry, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                with patch('neurobridge.adapters.storage.filesystem.os.fsync',
                           side_effect=OSError(errno.EIO, 'synthetic index sync failure')):
                    return real_update(session, entry, **kwargs)
            return real_update(session, entry, **kwargs)

        fault = (patch.object(self.repo, '_update_manifest', fail_sync_once) if failure_stage == 'sync'
                 else patch('neurobridge.adapters.storage.filesystem.os.replace', fail_once))
        with fault:
            result = await self.append('raw.device_frame', self.started + 1202000, 'failed-row')
        self.assertTrue(failed)
        self.assertFalse(result.persistence_guaranteed)
        self.assertEqual(manifest_path.read_bytes(), previous)
        self.assertTrue((self.root / 'sessions/rec-test/raw/000002.jsonl.partial').exists())
        self.assertFalse(list(self.root.rglob('*.tmp')))
        # The closed raw file must not make another category's batch fsync fail.
        self.assertTrue((await self.append('parsed.signal_batch', self.started + 1, 'parsed-2')).persistence_guaranteed)
        self.assertTrue((await self.append('raw.device_frame', self.started + 1202001, 'raw-3')).persistence_guaranteed)
        self.assertEqual(self.repo.storage_status().gap_count, 1)
        await self.repo.close()
        self.repo = self.make_repo()
        self.assertCountEqual(self.check_indexed_rows(), ['raw-1', 'raw-2', 'raw-3', 'parsed-1', 'parsed-2'])
        self.assertFalse(list(self.root.rglob('*.partial')))
        self.assertFalse(list((self.root / 'quarantine').iterdir()))

    async def test_manifest_replace_failure_preserves_index_and_recovers_data(self):
        await self.check_rotation_failure('manifest')

    async def test_segment_rename_failure_does_not_poison_later_batches(self):
        await self.check_rotation_failure('segment')

    async def test_manifest_sync_failure_preserves_index_and_recovers_data(self):
        await self.check_rotation_failure('sync')

    async def test_close_failure_still_finalizes_other_categories_and_records_gap(self):
        for kind in ('raw.device_frame', 'parsed.signal_batch', 'algorithm.result'):
            await self.append(kind, self.started, kind)
        real_update = self.repo._update_manifest

        def fail_raw(session, entry, **kwargs):
            if entry and entry['category'] == 'raw':
                raise OSError(errno.EIO, 'synthetic index failure')
            return real_update(session, entry, **kwargs)

        with patch.object(self.repo, '_update_manifest', fail_raw):
            await self.repo.close_session('rec-test')
        self.assertEqual(self.repo.storage_status().gap_count, 1)
        self.assertFalse(self.repo._segments)
        self.assertNotIn('endedAtMs', self.manifest())
        self.assertCountEqual(self.check_indexed_rows(), ['parsed.signal_batch', 'algorithm.result'])
        self.assertTrue(list(self.root.rglob('*.partial')))

    async def test_recovery_io_failure_retains_partial_for_next_start(self):
        await self.append('raw.device_frame', self.started, 'saved')
        with patch.object(self.repo, '_update_manifest', side_effect=OSError(errno.EIO, 'index unavailable')):
            await self.repo.close()
        self.assertEqual(self.repo.storage_status().gap_count, 1)
        with patch.object(SegmentedRecordingRepository, '_update_manifest', side_effect=OSError(errno.EIO, 'index unavailable')):
            self.repo = self.make_repo()
        self.assertEqual(self.repo.storage_status().state.value, 'error')
        self.assertTrue(list(self.root.rglob('*.partial')))
        self.assertFalse(list((self.root / 'quarantine').iterdir()))
        await self.repo.close()
        self.repo = self.make_repo()
        self.assertEqual(self.check_indexed_rows(), ['saved'])
        self.assertFalse(list(self.root.rglob('*.partial')))
