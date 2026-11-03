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
    async def test_controller_binds_the_shared_composition_root(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        gateway = SimpleNamespace(window_observer=None)
        adapter = object()
        container = SimpleNamespace(gateway=gateway, device_adapter=adapter)
        config = object()
        with patch.object(poc, 'build_container', return_value=container) as build:
            controller = poc.CaptureController(config)
        build.assert_called_once_with(config)
        self.assertIs(controller.gateway, gateway)
        self.assertIs(controller.adapter, adapter)
        self.assertEqual(gateway.window_observer, controller.log_algorithm_output)

    async def test_legacy_handler_preserves_local_poc_routes(self):
        import asyncio
        from types import SimpleNamespace
        controller = SimpleNamespace(snapshot=lambda: {'captureRunning':False}, capture=SimpleNamespace(logs=SimpleNamespace(snapshot=lambda: {'entries':[]})))
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), poc.handler_factory(controller, asyncio.get_running_loop(), poc.LogBuffer()))
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
            self.assertEqual(status, 200)
            for path in ['/capture/index.html', '/capture/app.js', '/b-client/index.html?endpoint=ws://127.0.0.1:8765/ws', '/api/status', '/api/logs']:
                status, headers, body = await asyncio.to_thread(request, path)
                self.assertEqual(status, 200, path)
                self.assertTrue(body)
        finally:
            await asyncio.to_thread(httpd.shutdown)
            httpd.server_close()
            thread.join(timeout=2)
