from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from neurobridge.business.gateway import ClientSession, Gateway, REPLAY_NOT_AVAILABLE_REASON
from neurobridge.business.recording import RecordingStore
from test_gateway import config


class CorruptRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.gateway = Gateway(config(self.root))
        await self.gateway.start()
        self.session = ClientSession()

    async def asyncTearDown(self):
        await self.gateway.stop()
        self.directory.cleanup()

    def recording(self, timestamp=1000):
        store = self.gateway.store
        recording_id = store.start(timestamp)
        store.save_raw_packet(stream='eeg', received_at_ms=1100, window_start_ms=600, window_end_ms=1200, value=b'e' * 20)
        source = {'receivedAtMsStart':1100, 'receivedAtMsEnd':1100, 'packetCount':1, 'windowStartMs':600, 'windowEndMs':1200}
        store.save_algorithm_events(algorithm={'attention':7}, computed_at_ms=1201, eeg_source=source, hr_source=None, valid=True, invalid_reasons=[])
        store.stop()
        return recording_id

    def file(self, recording_id, relative='raw/eeg.jsonl'):
        return self.root / 'sessions' / recording_id / relative

    async def request(self, action='getStatus', params=None):
        result = []
        async def send(item): result.append(item)
        await self.gateway.handle(self.session, json.dumps({'protocolVersion':'1.0','messageType':'request','requestId':action,'action':action,'params':params or {}}), send)
        return result[0]

    async def test_bad_raw_line_keeps_status_healthy_and_returns_existing_unavailable_error(self):
        recording_id = self.recording()
        file = self.file(recording_id)
        file.write_bytes(file.read_bytes() + b'\n')
        original = file.read_bytes()
        result = await self.request()
        with self.assertLogs('neurobridge.business.recording', level='WARNING') as logs:
            unavailable = await self.request('getLatest', {'streams':['eeg']})
        self.assertEqual(unavailable['data']['reason'], REPLAY_NOT_AVAILABLE_REASON)
        self.assertEqual(result['code'], 200)
        self.assertEqual(result['data']['result']['availableStreams'], ['status'])
        self.assertIn(str(file), logs.output[0])
        self.assertIn('line=2', logs.output[0])
        self.assertIn('JSONDecodeError', logs.output[0])
        self.assertEqual((await self.request('getLatest', {'streams':['eeg']}))['data']['reason'], REPLAY_NOT_AVAILABLE_REASON)
        self.assertEqual((await self.request('subscribe', {'streams':['eeg.raw']}))['code'], 503)
        self.assertEqual(file.read_bytes(), original)
        self.assertIsNone(self.gateway._replay_task)
        self.assertFalse(self.session.failed)

    async def test_bad_newest_and_explicit_pin_fall_back_to_healthy_older_session(self):
        older = self.recording(1000)
        newest = self.recording(2000)
        self.file(newest, 'algorithm/attention.jsonl').write_text('{"secret":"PRIVATE_VALUE",')
        self.gateway.config = replace(self.gateway.config, recording=replace(self.gateway.config.recording, replay_recording_id=newest))
        with self.assertLogs('neurobridge.business.recording', level='WARNING') as logs:
            self.assertEqual(self.gateway.replay_recording_id, older)
        self.assertNotIn('PRIVATE_VALUE', '\n'.join(logs.output))
        self.assertIn('algorithm/attention.jsonl', logs.output[0])
        await self.gateway.prepare_query()
        result = await self.request()
        self.assertEqual(result['code'], 200)
        self.assertIn('eeg', result['data']['result']['availableStreams'])
        latest = self.gateway.get_latest(self.session, {'streams':['eeg']}, start_replay=False)
        self.assertEqual(latest['payload']['algorithm']['attention'], 7)

    async def test_repaired_file_is_retried_without_restart(self):
        recording_id = self.recording()
        file = self.file(recording_id)
        good = file.read_bytes()
        file.write_bytes(b'\x00')
        self.assertEqual((await self.request())['code'], 200)
        self.assertIsNone(self.gateway.replay_recording_id)
        file.write_bytes(good)
        self.assertEqual(self.gateway.replay_recording_id, recording_id)
        await self.gateway.prepare_query()
        self.assertIn('eeg.raw', (await self.request())['data']['result']['availableStreams'])

    async def test_legacy_corruption_is_excluded_without_partial_data(self):
        file = self.root / 'algorithm' / 'rec-abcd.jsonl'
        file.write_text(json.dumps({'timestampMs':1200,'valid':True,'algorithm':{'attention':7}}) + '\ninvalid\n')
        with self.assertLogs('neurobridge.business.recording', level='WARNING') as logs:
            self.assertEqual(self.gateway.store.events('rec-abcd'), [])
        self.assertIn('line=2', logs.output[0])
        self.assertEqual((await self.request())['code'], 200)

    async def test_unicode_schema_and_permission_failures_do_not_escape_status(self):
        recording_id = self.recording()
        file = self.file(recording_id)
        for data in [b'\xff\n', b'[]\n', b'{}\n', b'{"windowEndMs":"bad","valid":true}\n']:
            with self.subTest(data=data):
                file.write_bytes(data)
                self.assertEqual((await self.request())['code'], 200)
                self.assertIsNone(self.gateway.replay_recording_id)
        with patch.object(RecordingStore, '_rows', side_effect=PermissionError(13, 'synthetic')):
            self.assertEqual((await self.request())['code'], 200)

    async def test_live_status_ignores_unreadable_history_and_retains_capture(self):
        recording_id = self.recording()
        self.file(recording_id).write_text('invalid\n')
        await self.gateway.update_status('connectionState', 'connected')
        active = self.gateway.store.recording_id
        with patch.object(self.gateway.store, 'events', side_effect=AssertionError('Live status must not read history')):
            result = await self.request()
        self.assertEqual(result['code'], 200)
        self.assertEqual(result['data']['result']['mode'], 'live')
        self.assertEqual(self.gateway.store.recording_id, active)
        await self.gateway.receive_packet('ff31', b'e' * 20)
        await self.gateway.update_status('connectionState', 'disconnected')
        self.assertEqual((await self.request())['code'], 200)

    async def test_corrupt_manifest_shapes_and_timestamps_do_not_break_selection(self):
        recording_id = self.recording()
        manifest = self.file(recording_id, 'manifest.json')
        for data in ['[]', '{"startedAtMs":"bad"}', '{"startedAtMs":{}}', 'invalid']:
            with self.subTest(data=data):
                manifest.write_text(data)
                self.assertEqual((await self.request())['code'], 200)
                self.assertEqual(self.gateway.replay_recording_id, recording_id)

    async def test_repeat_warning_is_throttled_and_interval_is_configurable(self):
        recording_id = self.recording()
        self.file(recording_id).write_text('PRIVATE_INVALID_JSON\n')
        with self.assertLogs('neurobridge.business.recording', level='WARNING') as logs:
            await self.request('getLatest', {'streams':['eeg']})
            await self.request('getLatest', {'streams':['eeg']})
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn('PRIVATE_INVALID_JSON', logs.output[0])
        self.gateway.store._diagnostics_interval = 0
        with self.assertLogs('neurobridge.business.recording', level='WARNING') as logs:
            self.gateway.store.events(recording_id)
            self.gateway.store.events(recording_id)
        self.assertEqual(len(logs.output), 2)
