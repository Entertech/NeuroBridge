from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import websockets

from neurobridge.ble.flowtime import FlowtimeAdapter
from neurobridge.ble.packets import DataWindow
from neurobridge.business.gateway import Gateway
from neurobridge.capture_control import CaptureControl, CaptureError, CaptureLogBuffer
from neurobridge.config import AlgorithmConfig, BleConfig, DownloadConfig, GatewayConfig, LoggingConfig, RecordingConfig, ServerConfig, load
from neurobridge.download import create_download_server
from neurobridge.northbound.websocket import create_server


def port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class FakeAdapter:
    def __init__(self, config, packet, status, ready, error, **kwargs):
        self.packet, self.status, self.ready = packet, status, ready
        self._client = SimpleNamespace(is_connected=False)
        self.started = asyncio.Event()
        self.fail_stop = False

    async def run(self):
        self._client.is_connected = True
        await self.ready()
        await self.status('connectionState', 'connected')
        self.started.set()
        await asyncio.Future()

    async def stop(self):
        if self.fail_stop:
            raise RuntimeError('synthetic stop failure')
        self._client.is_connected = False


async def http_request(port, path, method='GET', origin='null', extra=None):
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    headers = {'Host': f'127.0.0.1:{port}', 'Connection': 'close'}
    if origin is not None:
        headers['Origin'] = origin
    headers.update(extra or {})
    request = f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{key}: {value}\r\n' for key, value in headers.items()) + '\r\n'
    writer.write(request.encode())
    await writer.drain()
    result = await asyncio.wait_for(reader.read(), timeout=2)
    writer.close()
    await writer.wait_closed()
    head, body = result.split(b'\r\n\r\n', 1)
    lines = head.decode().split('\r\n')
    result_headers = dict((key.lower(), value.strip()) for key, value in (line.split(':', 1) for line in lines[1:]))
    return int(lines[0].split()[1]), result_headers, body


class CaptureHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.port = port()
        config = GatewayConfig(ServerConfig('127.0.0.1', port(), '/neurobridge/v1/ws'), BleConfig(True, None, 'profile', 1, 0), RecordingConfig(root / 'recordings', None, None, 1), AlgorithmConfig(False, ()), DownloadConfig(True, '127.0.0.1', self.port), LoggingConfig(root / 'logs'))
        self.gateway = Gateway(config)
        self.created = []
        def factory(*args, **kwargs):
            adapter = FakeAdapter(*args, **kwargs)
            self.created.append(adapter)
            return adapter
        self.capture = CaptureControl(self.gateway, factory)
        self.server = await create_download_server(self.gateway, self.capture)
        self.websocket = await create_server(self.gateway)
        pdf = root / 'fixture.pdf'
        pdf.write_bytes(b'%PDF-1.4\n%%EOF\n')
        self.gateway.store._capture_package_pdf = pdf

    async def asyncTearDown(self):
        for adapter in self.created:
            adapter.fail_stop = False
        self.server.close()
        self.websocket.close()
        await self.server.wait_closed()
        await self.websocket.wait_closed()
        await self.capture.shutdown()
        await self.gateway.stop()
        self.temporary.cleanup()

    async def start_capture(self):
        status, headers, body = await http_request(self.port, '/api/capture/start', 'POST')
        self.assertEqual(status, 200, body)
        self.assertEqual(headers['access-control-allow-origin'], 'null')
        await asyncio.wait_for(self.created[-1].started.wait(), timeout=1)
        return json.loads(body)

    async def test_start_is_idempotent_across_simultaneous_operator_requests(self):
        await asyncio.gather(self.start_capture(), self.start_capture())
        self.assertEqual(len(self.created), 1)
        status, _, body = await http_request(self.port, '/api/status')
        result = json.loads(body)
        self.assertEqual(set(result), {'protocolVersion', 'code', 'data', 'message'})
        self.assertTrue(result['data']['captureRunning'])
        self.assertEqual(result['data']['connectionState'], 'connected')
        self.assertIsNone(result['data']['exportUrl'])

    async def test_stop_completes_recording_and_preserves_both_listeners(self):
        await self.start_capture()
        await self.gateway.receive_packet('ff31', b'e' * 20)
        recording_id = self.gateway.store.recording_id
        code, _, _ = await http_request(self.port, f'/downloads/recordings/{recording_id}.zip')
        self.assertEqual(code, 409)
        code, _, body = await http_request(self.port, '/api/capture/stop', 'POST')
        self.assertEqual(code, 200)
        stopped = json.loads(body)['data']
        self.assertFalse(stopped['captureRunning'])
        self.assertIsNone(stopped['recordingId'])
        self.assertEqual(stopped['exportRecordingId'], recording_id)
        code, _, body = await http_request(self.port, stopped['exportUrl'])
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(b'PK'))
        url = f'ws://127.0.0.1:{self.gateway.config.server.port}/neurobridge/v1/ws'
        async with websockets.connect(url, subprotocols=['neurobridge.v1'], ping_interval=None, origin='http://127.0.0.1:8080') as client:
            await client.send(json.dumps({'protocolVersion': '1.0', 'messageType': 'request', 'requestId': 'status', 'action': 'getStatus', 'params': {}}))
            result = json.loads(await client.recv())
            self.assertEqual(result['data']['result']['mode'], 'replay')
        await http_request(self.port, '/api/capture/stop', 'POST')
        await self.start_capture()
        self.assertEqual(len(self.created), 2)
        self.assertNotEqual(self.gateway.store.recording_id, recording_id)

    async def test_file_origin_preflight_is_allowed_but_unknown_origin_cannot_start(self):
        code, headers, _ = await http_request(self.port, '/api/capture/start', 'OPTIONS', extra={'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Private-Network': 'true'})
        self.assertEqual(code, 204)
        self.assertEqual(headers['access-control-allow-private-network'], 'true')
        self.assertEqual(headers['access-control-allow-origin'], 'null')
        code, headers, body = await http_request(self.port, '/api/capture/start', 'POST', origin='https://untrusted.example')
        self.assertEqual(code, 403)
        self.assertNotIn('access-control-allow-origin', headers)
        self.assertEqual(json.loads(body)['code'], 403)
        self.assertFalse(self.created)

    async def test_bad_method_body_and_disabled_capture_are_rejected_without_side_effects(self):
        code, _, _ = await http_request(self.port, '/api/capture/start', 'GET')
        self.assertEqual(code, 405)
        code, _, _ = await http_request(self.port, '/api/capture/start', 'POST', extra={'Content-Length': '1'})
        self.assertEqual(code, 400)
        self.gateway.config = replace(self.gateway.config, ble=replace(self.gateway.config.ble, enabled=False))
        code, _, body = await http_request(self.port, '/api/capture/start', 'POST')
        self.assertEqual(code, 503)
        self.assertIn('ble.enabled=false', json.loads(body)['message'])
        self.assertFalse(self.created)
        self.gateway.config = replace(self.gateway.config, download=replace(self.gateway.config.download, capture_control_enabled=False))
        code, _, _ = await http_request(self.port, '/api/status')
        self.assertEqual(code, 404)
        code, _, _ = await http_request(self.port, '/downloads')
        self.assertEqual(code, 200)

    async def test_installed_package_serves_ubuntu_assets_and_shared_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'ubuntu/capture').mkdir(parents=True)
            (root / 'b-client-test').mkdir()
            (root / 'ubuntu/capture/app.js').write_text('installed Ubuntu console')
            (root / 'b-client-test/version.js').write_text('shared protocol version')
            with patch('neurobridge.download.__file__', str(root / 'venv/site-packages/neurobridge/download.py')), patch('neurobridge.download.DEPLOYED_WEB_ROOT', root):
                for url, expected in [('/capture/app.js', b'installed Ubuntu console'), ('/b-client-test/version.js', b'shared protocol version')]:
                    code, _, body = await http_request(self.port, url)
                    self.assertEqual(code, 200)
                    self.assertEqual(body, expected)

    async def test_existing_task_survives_an_abruptly_closed_http_client(self):
        await self.start_capture()
        _, writer = await asyncio.open_connection('127.0.0.1', self.port)
        writer.write(b'GET /api/status HTTP/1.1\r\nHost: localhost\r\nOrigin: null\r\n\r\n')
        await writer.drain()
        writer.transport.abort()
        await asyncio.sleep(0.01)
        self.assertTrue(self.capture.running)
        self.assertTrue(self.gateway.live)

    async def test_static_assets_are_hosted_and_traversal_is_rejected(self):
        for path in ['/capture/index.html', '/capture/app.js', '/capture/styles.css', '/b-client-test/index.html', '/b-client-test/index.html?endpoint=ws%3A%2F%2F127.0.0.1%3A8765%2Fws']:
            code, _, body = await http_request(self.port, path, origin=None)
            self.assertEqual(code, 200, path)
            self.assertTrue(body)
        for path in ['/capture/../config/gateway.toml', '/capture/%2e%2e/README.md']:
            code, _, _ = await http_request(self.port, path)
            self.assertEqual(code, 404)

    async def test_api_query_rejection_preserves_response_envelope(self):
        code, _, body = await http_request(self.port, '/api/status?unexpected=1')
        self.assertEqual(code, 400)
        self.assertEqual(set(json.loads(body)), {'protocolVersion', 'code', 'data', 'message'})

    async def test_window_logs_keep_counts_and_metrics_without_raw_bytes_or_waveforms(self):
        await self.start_capture()
        await self.gateway.receive_packet('ff31', b'e' * 20)
        window = self.gateway.assembler.flush()
        await self.capture.observe_window(window, {'attention': 7, 'eeg': {'wave': ['PRIVATE_WAVE'], 'bandPower': {'alpha': 1}}, 'bytesBase64': 'PRIVATE_RAW'}, [], True)
        code, _, body = await http_request(self.port, '/api/logs')
        self.assertEqual(code, 200)
        logs = json.loads(body)['data']['entries']
        self.assertTrue(any('eegPackets=1' in item['message'] for item in logs))
        self.assertIn('attention', body.decode())
        self.assertNotIn('PRIVATE_WAVE', body.decode())
        self.assertNotIn('PRIVATE_RAW', body.decode())

    async def test_stop_failure_is_reported_and_cannot_start_a_second_link(self):
        await self.start_capture()
        self.created[0].fail_stop = True
        code, _, body = await http_request(self.port, '/api/capture/stop', 'POST')
        self.assertEqual(code, 503)
        self.assertIn('could not be confirmed', json.loads(body)['message'])
        code, _, _ = await http_request(self.port, '/api/capture/start', 'POST')
        self.assertEqual(code, 503)
        self.assertEqual(len(self.created), 1)
        self.created[0].fail_stop = False
        code, _, _ = await http_request(self.port, '/api/capture/stop', 'POST')
        self.assertEqual(code, 200)


class CaptureSupportTests(unittest.IsolatedAsyncioTestCase):
    def test_log_buffer_is_bounded(self):
        buffer = CaptureLogBuffer()
        for i in range(300):
            buffer.append(str(i) + 'x' * 3000)
        entries = buffer.snapshot()['entries']
        self.assertEqual(len(entries), 200)
        self.assertEqual(len(entries[-1]['message']), 2048)

    async def test_failed_device_stop_command_still_disconnects_the_link(self):
        adapter = FlowtimeAdapter(BleConfig(True, None, 'profile', 1, 0), AsyncMock(), AsyncMock(), AsyncMock())
        client = SimpleNamespace(is_connected=True, write_gatt_char=AsyncMock(side_effect=OSError('synthetic command failure')))
        async def disconnect(): client.is_connected = False
        client.disconnect = AsyncMock(side_effect=disconnect)
        adapter._client = client
        await adapter.stop()
        client.disconnect.assert_awaited_once()

    async def test_notifications_after_stop_cannot_feed_a_completed_session(self):
        packet = AsyncMock()
        adapter = FlowtimeAdapter(BleConfig(True, None, 'profile', 1, 0), packet, AsyncMock(), AsyncMock())
        callbacks = {}
        async def start_notify(characteristic, callback):
            callbacks[characteristic] = callback
        adapter._client = SimpleNamespace(start_notify=start_notify, services=SimpleNamespace(get_characteristic=lambda _: None))
        await adapter._subscribe()
        adapter._stopping = True
        for callback in callbacks.values():
            callback(None, bytearray(b'late-notification'))
        await adapter._drain_callbacks()
        packet.assert_not_awaited()

    def test_capture_origin_settings_reject_wildcards_and_non_origins(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'gateway.toml'
            for value in ['["*"]', '["http://example.test/path"]', '["http://user:password@example.test"]']:
                file.write_text('[data_source]\ntype="bluetooth"\n' + f'[download]\ncapture_allowed_origins = {value}\n')
                with self.assertRaises(ValueError): load(file)
            file.write_text('[data_source]\ntype="bluetooth"\n' + '[download]\ncapture_allowed_origins = ["null", "http://127.0.0.1:8080"]\n')
            self.assertEqual(load(file).download.capture_allowed_origins, ('null', 'http://127.0.0.1:8080'))
