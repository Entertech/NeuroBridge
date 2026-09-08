from __future__ import annotations

import asyncio
from dataclasses import replace
import errno
import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from neurobridge.config import load, LoggingConfig
from neurobridge.configuration.runtime import load_runtime_config
from neurobridge.profiles.resolver import detect_runtime_platform, resolve_profile
from neurobridge.adapters.storage.filesystem import SegmentedRecordingRepository
from neurobridge.adapters.sources.bluetooth_bleak import BluetoothBleakSource
from neurobridge.adapters.parsers import HeadsetRev181Parser
from neurobridge.application.windowing import SignalWindowAssembler
from neurobridge.domain.raw import RawChunk
from neurobridge.logging_setup import configure_logging
from neurobridge.ports.recording import PersistenceRecord

ROOT = Path(__file__).resolve().parents[2]


class RequirementConfigurationTests(unittest.TestCase):
    def test_wrong_types_are_rejected_without_coercion(self):
        cases = [('algorithm', 'enabled', '"false"'), ('algorithm', 'command', '"bridge"'),
                 ('server', 'port', '8765.5'), ('server', 'port', 'true'),
                 ('download', 'enabled', '"false"'), ('recording', 'replay_speed', 'nan'),
                 ('serial', 'handshake_timeout_ms', '"1000"'), ('logging', 'backup_count', 'false')]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'config.toml'
            for section, field, value in cases:
                with self.subTest(field=field, value=value):
                    path.write_text(f'[data_source]\ntype="serial"\n[{section}]\n{field}={value}\n')
                    with self.assertRaisesRegex(ValueError, f'{section}.{field}'):
                        load(path)

    def test_window_contract_cannot_be_changed_by_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'config.toml'
            path.write_text('[data_source]\ntype="serial"\nwindow_interval_ms=1000\n')
            with self.assertRaisesRegex(ValueError, '600'):
                load(path)

    def test_detected_kylin_version_fails_closed(self):
        cfg = load(ROOT/'config/gateway.project.toml.example')
        with patch('neurobridge.profiles.resolver.sys.platform', 'linux'), patch('neurobridge.profiles.resolver.platform.machine', return_value='x86_64'):
            for version in ('', '11', '9'):
                runtime = detect_runtime_platform({'ID':'kylin', 'VERSION_ID':version})
                with self.assertRaisesRegex(ValueError, 'V10'):
                    resolve_profile(cfg, runtime)
            self.assertEqual(resolve_profile(cfg, detect_runtime_platform({'ID':'kylin','VERSION_ID':'10'})).delivery_stage, 'M1')

    def test_runtime_layering_override_gate_and_packaged_profile(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); defaults=root/'defaults.toml'; system=root/'system.toml'; override=root/'override.toml'
            defaults.write_text('profile="kylin_headset_local"\n[data_source]\ntype="serial"\n')
            system.write_text('[server]\nport=9000\n')
            override.write_text('[server]\nport=9100\n')
            self.assertEqual(load_runtime_config(system, defaults_path=defaults).server.port, 9000)
            with self.assertRaisesRegex(ValueError, 'development'):
                load_runtime_config(system, defaults_path=defaults, override_path=override)
            self.assertEqual(load_runtime_config(system, defaults_path=defaults, override_path=override, development=True).server.port, 9100)
            system.write_text('profile="windows_headset_local"\n')
            with self.assertRaisesRegex(ValueError, 'packaged'):
                load_runtime_config(system, defaults_path=defaults)

    def test_rotation_is_bounded_without_system_logrotate(self):
        root_logger=logging.getLogger(); previous=root_logger.handlers[:]; previous_level=root_logger.level
        root_logger.handlers=[]
        try:
            with tempfile.TemporaryDirectory() as d:
                configure_logging(LoggingConfig(Path(d), max_bytes=128, backup_count=2))
                handler=root_logger.handlers[0]
                for i in range(40):
                    handler.emit(logging.LogRecord('test',logging.INFO,__file__,1,'synthetic record %s',(i,),None))
                handler.flush()
                files=list(Path(d).glob('neurobridge.log*'))
                self.assertEqual(len(files),3)
                self.assertTrue(all(p.stat().st_size <= 160 for p in files))
        finally:
            for handler in root_logger.handlers: handler.close()
            root_logger.handlers=previous; root_logger.setLevel(previous_level)

    def test_batch_metadata_preserves_counts_sequences_and_source(self):
        parser=HeadsetRev181Parser(); assembler=SignalWindowAssembler()
        raw=b'\xaa\xaa\xaa\x1c\xff\xff'+bytes(range(18))+b'\x48\xbb\xbb\xbb'
        outcome=parser.feed(RawChunk('serial','serial.read',raw,1000,100,'session','trace'))
        for signal in outcome.signals:
            assembler.append(signal,device_protocol='headset_rev181',connection_session_id='session',recording_session_id='rec',source_type='serial')
        batch=assembler.flush()
        self.assertEqual(batch.sample_counts, {'eeg':6,'hr':1})
        self.assertEqual(batch.source_type,'serial'); self.assertEqual(batch.schema_version,1)
        self.assertEqual(batch.sequence_range,(65535,65535)); self.assertEqual(batch.received_at_range_ms,(1000,1000))
        self.assertTrue(all(s.unit is None for s in batch.signals))


class StorageRequirementTests(unittest.IsolatedAsyncioTestCase):
    async def test_errno_classification_and_success_clock(self):
        for code, state, reason in ((errno.ENOSPC,'full','no_space'),(errno.EDQUOT,'full','quota_exceeded'),
            (errno.EROFS,'error','read_only'),(errno.EACCES,'error','permission_denied'),(errno.ENOENT,'error','path_unavailable'),(errno.EIO,'error','io_error')):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as d:
                repo=SegmentedRecordingRepository(d,warning_threshold_bytes=100,critical_threshold_bytes=10,recovery_margin_bytes=1)
                try:
                    def fail(_): raise OSError(code,'synthetic failure')
                    repo._write=fail
                    receipt=await repo.confirm(repo.try_append(PersistenceRecord(1,'rec','raw.device_frame',100,'frame',{})))
                    status=repo.storage_status()
                    self.assertEqual((status.state.value,receipt.reason),(state,reason))
                    self.assertIsNone(status.last_success_at_ms)
                    self.assertFalse(receipt.persistence_guaranteed)
                finally:
                    await repo.close()

    async def test_recovery_retains_valid_prefix_before_corrupt_line(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'sessions/rec/raw/000001.jsonl.partial';path.parent.mkdir(parents=True)
            row=json.dumps({'capturedAtMs':100,'recordType':'raw.device_frame'})+'\n'
            path.write_text(row+'not json\n'+row)
            repo=SegmentedRecordingRepository(d)
            try:
                final=path.with_suffix('')
                self.assertEqual(final.read_text(),row)
                manifest=json.loads((path.parents[1]/'manifest.json').read_text())
                self.assertEqual(manifest['segments'][0]['status'],'recovered')
                self.assertEqual(manifest['segments'][0]['recordCount'],1)
            finally: await repo.close()

    async def test_cleanup_audits_skipped_and_failed_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            repo=SegmentedRecordingRepository(d,auto_cleanup_enabled=True)
            try:
                for name, extra in [('retained',{'retain':True}),('eligible',{})]:
                    session=Path(d)/'sessions'/name;session.mkdir()
                    (session/'manifest.json').write_text(json.dumps({'endedAtMs':1,**extra}))
                with patch('neurobridge.adapters.storage.filesystem.shutil.disk_usage') as usage, patch('neurobridge.adapters.storage.filesystem.shutil.rmtree',side_effect=OSError(errno.EACCES,'synthetic')):
                    usage.return_value.free=0
                    self.assertEqual(repo.cleanup_completed_sessions(),())
                rows=[json.loads(line) for line in (Path(d)/'cleanup-audit.jsonl').read_text().splitlines()]
                self.assertIn('skipped',[r['outcome'] for r in rows]); self.assertIn('selected',[r['outcome'] for r in rows]); self.assertIn('failed',[r['outcome'] for r in rows])
            finally: await repo.close()


class BleControlRequirementTests(unittest.IsolatedAsyncioTestCase):
    async def test_ble_control_is_session_bound_idempotent_and_closes_after_failure(self):
        cfg=load(ROOT/'config/gateway.ubuntu.toml.example')
        async def ready(): pass
        source=BluetoothBleakSource(cfg.ble,ready)
        commands=[]
        class Client:
            is_connected=True
            async def write_gatt_char(self,characteristic,value,**kwargs):
                commands.append(value)
                if value==b'\x06': raise OSError('synthetic stop failure')
            async def disconnect(self): self.is_connected=False
        client=Client();source._adapter._client=client;source._session_id='a'
        self.assertEqual((await source.control.start_stream('a')).outcome,'started')
        self.assertEqual((await source.control.start_stream('a')).outcome,'alreadyStreaming')
        source._session_id='b'
        self.assertEqual((await source.control.stop_stream('a')).outcome,'staleSession')
        await source.control.start_stream('b'); await source.stop()
        self.assertFalse(client.is_connected)
        self.assertEqual(commands,[b'\x05',b'\x05',b'\x06'])

    async def test_waiting_control_does_not_write_new_connection(self):
        cfg=load(ROOT/'config/gateway.ubuntu.toml.example')
        async def ready(): pass
        source=BluetoothBleakSource(cfg.ble,ready);source._session_id='a'
        await source._adapter._io_lock.acquire()
        pending=asyncio.create_task(source.control.start_stream('a'));await asyncio.sleep(0)
        source._session_id='b'; source._adapter._io_lock.release()
        self.assertEqual((await pending).outcome,'writeFailed')
