from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from neurobridge.ble.flowtime import BATTERY, FF32, FF51, REQUIRED_NOTIFICATION_CHARACTERISTICS, FlowtimeAdapter, battery_percent_from_packet
from neurobridge.business.gateway import Gateway
from test_gateway import config


class DeviceTelemetryTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, packet=None, status=None):
        return FlowtimeAdapter(config(Path('/tmp')).ble, packet or AsyncMock(), status or AsyncMock(), AsyncMock())

    def test_battery_protocol_endpoints_example_and_invalid_packets(self):
        for value, expected in [(b'\x00', 0), (b'\x52', 82), (b'\x64', 100), (b'', None), (b'\x65', None), (b'\x52\x00', None)]:
            self.assertEqual(battery_percent_from_packet(value), expected)

    async def test_optional_notify_failure_preserves_required_streams_and_initial_read(self):
        handlers = {}
        async def start_notify(characteristic, callback):
            if characteristic == BATTERY:
                raise RuntimeError('notification not supported')
            handlers[characteristic] = callback
        adapter = self.adapter()
        adapter._client = SimpleNamespace(
            services=SimpleNamespace(get_characteristic=lambda _: object()),
            start_notify=start_notify, is_connected=True,
            read_gatt_char=AsyncMock(return_value=bytearray(b'\x52')),
        )
        with self.assertLogs('neurobridge.ble.flowtime', level='WARNING'):
            await adapter._subscribe()
        self.assertEqual(set(handlers), set(REQUIRED_NOTIFICATION_CHARACTERISTICS))
        await adapter._read_battery()
        adapter.packet.assert_awaited_once_with('battery', b'\x52')
        adapter.status.assert_awaited_once_with('batteryPercent', 82)
        handlers[FF51](None, bytearray(b'\x50'))
        handlers[FF32](None, bytearray(b'\x00\x01'))
        await adapter._drain_callbacks()
        adapter.packet.assert_any_await('ff51', b'\x50')
        adapter.packet.assert_any_await('ff32', b'\x00\x01')
        adapter.status.assert_any_await('wearState', 'notWorn')

    async def test_failed_or_stale_battery_read_cannot_publish_false_state(self):
        adapter = self.adapter()
        client = SimpleNamespace(services=SimpleNamespace(get_characteristic=lambda _: object()), is_connected=True, read_gatt_char=AsyncMock(side_effect=TimeoutError))
        adapter._client = client
        with self.assertLogs('neurobridge.ble.flowtime', level='WARNING'):
            await adapter._read_battery()
        adapter.status.assert_not_awaited()
        client.read_gatt_char = AsyncMock(return_value=b'\x52')
        client.is_connected = False
        await adapter._read_battery()
        adapter.status.assert_not_awaited()

    async def test_initial_battery_processing_failure_does_not_disconnect_capture(self):
        adapter = self.adapter(packet=AsyncMock(side_effect=OSError('disk unavailable')))
        adapter._client = SimpleNamespace(services=SimpleNamespace(get_characteristic=lambda _: object()), is_connected=True, read_gatt_char=AsyncMock(return_value=b'\x52'))
        with self.assertLogs('neurobridge.ble.flowtime', level='ERROR'):
            await adapter._read_battery()
        self.assertTrue(adapter._client.is_connected)

    async def test_raw_status_bytes_are_preserved_separately_and_replay_stays_eeg_hr(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = Gateway(config(Path(directory)))
            await gateway.start()
            try:
                await gateway.update_status('connectionState', 'connected')
                recording_id = gateway.store.recording_id
                adapter = self.adapter(packet=gateway.receive_packet, status=gateway.update_status)
                await adapter._process_notification(FF32, b'\x00\x00')
                await adapter._process_notification(BATTERY, b'\x52')
                self.assertEqual(gateway.status_result()['wearState'], 'worn')
                self.assertEqual(gateway.status_result()['batteryPercent'], 82)
                self.assertIsNone(gateway.assembler.window_end_ms)
                for name, value in [('wear', b'\x00\x00'), ('battery', b'\x52')]:
                    row = json.loads((Path(directory) / 'sessions' / recording_id / 'raw' / f'{name}.jsonl').read_text())
                    self.assertEqual(base64.b64decode(row['bytesBase64']), value)
                    self.assertEqual(row['sessionId'], recording_id)
                    self.assertTrue(row['valid'])
                    self.assertLess(row['windowStartMs'], row['windowEndMs'])
                self.assertEqual(gateway.store.events(recording_id), [])
                await adapter._process_notification(FF32, b'\x01\x00')
                self.assertEqual(gateway.status['wearState'], 'notWorn')
                await gateway.update_status('connectionState', 'disconnected')
                self.assertEqual(gateway.status['wearState'], 'unknown')
                self.assertIsNone(gateway.status['batteryPercent'])
                manifest = json.loads((Path(directory) / 'sessions' / recording_id / 'manifest.json').read_text())
                self.assertIn('raw/wear.jsonl', [item['path'] for item in manifest['files']])
            finally:
                await gateway.stop()
