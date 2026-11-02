import importlib.util
from pathlib import Path
import sys
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_capture_control import FakeAdapter

spec = importlib.util.spec_from_file_location('mac_poc', Path(__file__).resolve().parents[1] / 'mac/poc_server.py')
poc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poc)


class MacCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_lifecycle_allows_restart_and_rejects_active_export(self):
        import asyncio
        import tempfile
        from neurobridge.config import AlgorithmConfig, BleConfig, GatewayConfig, RecordingConfig, ServerConfig
        from test_capture_control import port
        with tempfile.TemporaryDirectory() as directory:
            config = GatewayConfig(ServerConfig('127.0.0.1', port(), '/neurobridge/v1/ws'), BleConfig(True, None, 'profile', 1, 0), RecordingConfig(Path(directory), None, None, 1), AlgorithmConfig(False, ()))
            controller = poc.CaptureController(config)
            controller.capture.adapter_factory = FakeAdapter
            try:
                await controller.start()
                first = controller.capture._adapter
                await first.started.wait()
                await controller.gateway.receive_packet('ff31', b'e' * 20)
                with self.assertRaises(ValueError):
                    await controller.export_current_recording()
                await controller.stop()
                self.assertTrue(controller.server.is_serving())
                self.assertEqual(controller.snapshot()['exportUrl'], '/api/recordings/current/download')
                await controller.start()
                self.assertIsNot(controller.capture._adapter, first)
            finally:
                await controller.shutdown()

    async def test_legacy_handler_relative_assets_and_origin_checks(self):
        import asyncio
        from types import SimpleNamespace
        controller = SimpleNamespace(snapshot=lambda: {'captureRunning':False}, capture=SimpleNamespace(logs=SimpleNamespace(snapshot=lambda: {'entries':[]})))
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), poc.handler_factory(controller, asyncio.get_running_loop()))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        def request(path, method='GET', origin='null'):
            connection = HTTPConnection('127.0.0.1', httpd.server_port, timeout=2)
            connection.request(method, path, headers={'Origin': origin})
            result = connection.getresponse()
            body = result.read()
            status, headers = result.status, dict(result.getheaders())
            connection.close()
            return status, headers, body
        try:
            status, headers, _ = await asyncio.to_thread(request, '/')
            self.assertEqual(status, 302)
            self.assertEqual(headers['Location'], '/capture/index.html')
            for path in ['/capture/index.html', '/capture/app.js', '/b-client-test/index.html?endpoint=ws://127.0.0.1:8765/ws', '/api/status', '/api/logs']:
                status, headers, body = await asyncio.to_thread(request, path)
                self.assertEqual(status, 200, path)
                self.assertEqual(headers['Access-Control-Allow-Origin'], 'null')
                self.assertTrue(body)
            status, _, _ = await asyncio.to_thread(request, '/api/capture/start', 'POST', 'https://foreign.example')
            self.assertEqual(status, 403)
            status, _, _ = await asyncio.to_thread(request, '/api/capture/start', 'OPTIONS')
            self.assertEqual(status, 204)
        finally:
            await asyncio.to_thread(httpd.shutdown)
            httpd.server_close()
            thread.join(timeout=2)
