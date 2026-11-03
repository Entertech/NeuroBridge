"""Exercise Ubuntu BLE through the production composition and segmented writer."""
import asyncio
import base64
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from neurobridge.ble.flowtime import BATTERY, FF21, FF31, FF32, FF51
from neurobridge.bootstrap import build_container
from neurobridge.adapters.storage.archive import ArchiveReplayReader, SegmentedArchive
from neurobridge.business.recording import RecordingStore
from neurobridge.capture_control import CaptureControl
from neurobridge.config import load
from neurobridge.profiles.resolver import RuntimePlatform

ROOT = Path(__file__).resolve().parents[2]


class UbuntuPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_notifications_telemetry_restart_and_replay_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(ROOT / 'config/gateway.ubuntu.toml.example')
            config = replace(config, recording=replace(config.recording, directory=Path(directory), replay_enabled=False),
                             algorithm=replace(config.algorithm, enabled=False))
            runtime = RuntimePlatform('ubuntu', 'x86_64')
            container = build_container(config, runtime)
            gateway = container.gateway
            operations, clients = [], []

            class Client:
                def __init__(self, _, disconnected_callback):
                    self.is_connected = False
                    self.callback = disconnected_callback
                    self.handlers = {}
                    self.services = SimpleNamespace(get_characteristic=lambda _: object())
                    clients.append(self)

                async def connect(self):
                    self.is_connected = True

                async def start_notify(self, characteristic, callback):
                    self.handlers[characteristic] = callback
                    operations.append(('notify', characteristic))
                    if characteristic == FF31:
                        # Firmware may emit while StartNotify is still pending.
                        if gateway.live:
                            raise AssertionError("connected published before capture start")
                        self.assert_recording_open = gateway.store.recording_id
                        callback(None, bytearray(b'e' * 20))
                        await asyncio.sleep(0)

                async def write_gatt_char(self, characteristic, value, **_):
                    operations.append(('control', bytes(value)))
                    if value == b'\x05':
                        if not {FF31, FF32, FF51}.issubset(self.handlers):
                            raise AssertionError('capture started before notifications')
                        self.handlers[FF32](None, bytearray(b'\x00\x00'))
                        self.handlers[FF51](None, bytearray(b'\x50'))

                async def read_gatt_char(self, characteristic):
                    return b'\x52'

                async def disconnect(self):
                    self.is_connected = False
                    self.callback(self)

            candidate = SimpleNamespace(name=config.ble.device_name,
                                        metadata={'uuids': [config.ble.model_nbr_uuid]}, rssi=-50)
            fake = SimpleNamespace(BleakClient=Client,
                BleakScanner=SimpleNamespace(discover=AsyncMock(return_value=[candidate])))
            first = [container.device_adapter]
            control = CaptureControl(gateway, pipeline_factory=lambda: first.pop() if first else
                                     build_container(config, runtime, gateway=gateway).device_adapter)
            await gateway.start()
            try:
                with patch.dict(sys.modules, {'bleak': fake}):
                    sessions = []
                    for _ in range(2):
                        await control.start()
                        async with asyncio.timeout(3):
                            while gateway.status['batteryPercent'] != 82:
                                await asyncio.sleep(.01)
                        self.assertEqual(gateway.status['wearState'], 'worn')
                        self.assertEqual(gateway.mode(), 'live')
                        sessions.append(gateway.store.recording_id)
                        self.assertEqual(clients[-1].assert_recording_open, sessions[-1])
                        await control.stop()
                        self.assertFalse(clients[-1].is_connected)
                        self.assertIsNone(gateway.status['batteryPercent'])
                        self.assertEqual(gateway.status['wearState'], 'unknown')
                    self.assertNotEqual(*sessions)
                self.assertEqual(operations.count(('control', b'\x05')), 2)
                self.assertEqual(operations.count(('control', b'\x06')), 2)
                self.assertEqual(gateway.mode(), 'live')
                with patch.object(gateway.replay_reader, 'inspect', side_effect=AssertionError('replay disabled')):
                    await gateway.prepare_query()
                    self.assertFalse(gateway.replay_available)
                rows = []
                for path in (Path(directory) / 'sessions' / sessions[0] / 'raw').glob('*.jsonl'):
                    rows.extend(json.loads(line) for line in path.read_text().splitlines())
                raw = [(row['payload']['channel'], base64.b64decode(row['payload']['rawBytes']['bytesBase64']))
                       for row in rows if row['recordType'] == 'raw.device_frame']
                self.assertIn(('ff31', b'e' * 20), raw)
                self.assertIn(('ff32', b'\x00\x00'), raw)
                self.assertIn(('battery', b'\x52'), raw)
            finally:
                await control.shutdown()
                await gateway.stop()

    def test_legacy_ubuntu_configuration_keeps_its_addresses_and_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gateway.toml'
            path.write_text('[server]\nhost="192.168.88.10"\n[ble]\nenabled=false\n'
                            '[recording]\nreplay_enabled=false\n[algorithm]\nevaluation_timeout_seconds=3\n')
            original = path.read_bytes()
            config = load(path)
            container = build_container(config, RuntimePlatform('ubuntu', 'x86_64'))
            self.assertEqual(container.profile.profile_id, 'ubuntu_headband_wired')
            self.assertEqual(config.server.host, '192.168.88.10')
            self.assertEqual(config.algorithm.request_timeout_ms, 3000)
            self.assertFalse(container.gateway.supports_replay)
            self.assertEqual(path.read_bytes(), original)

    async def test_corrupt_segment_is_excluded_and_legacy_replay_remains_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = RecordingStore(root)
            healthy = legacy.start(1000)
            legacy.save_algorithm(timestamp_ms=1200, valid=True, invalid_reasons=[],
                                  algorithm={'attention': 7})
            legacy.stop()
            broken = root / 'sessions/rec-bad/parsed/000001.jsonl'
            broken.parent.mkdir(parents=True)
            broken.write_bytes(b'not json\n')
            archive = SegmentedArchive(root)
            reader = ArchiveReplayReader(archive)
            with self.assertLogs('neurobridge.business.recording', level='WARNING') as logs:
                selected, streams, latest, timestamp = await reader.inspect('rec-bad')
            self.assertEqual(selected, healthy)
            self.assertEqual(latest, {'attention': 7})
            self.assertEqual(timestamp, 1200)
            self.assertIn('eeg', streams)
            self.assertIn('line=1', '\n'.join(logs.output))
            self.assertTrue(broken.exists())
