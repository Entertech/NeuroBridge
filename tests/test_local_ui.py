from __future__ import annotations

import asyncio
from pathlib import Path
import socket
import tempfile
import unittest

from neurobridge.config import (
    AlgorithmConfig,
    BleConfig,
    GatewayConfig,
    LocalUiConfig,
    RecordingConfig,
    ServerConfig,
)
from neurobridge.northbound.local_ui import create_local_ui_server


def unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class LocalUiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        for filename in ("index.html", "app.js", "version.js", "styles.css"):
            (root / filename).write_text(f"asset:{filename}\n", encoding="utf-8")
        capture = root / "capture"
        capture.mkdir()
        for filename in ("index.html", "app.js", "styles.css"):
            (capture / filename).write_text(f"capture:{filename}\n", encoding="utf-8")
        self.ui_port = unused_port()
        self.websocket_port = unused_port()
        self.config = GatewayConfig(
            ServerConfig("127.0.0.1", self.websocket_port, "/neurobridge/v1/ws"),
            BleConfig(False, None, "0000ff10-1212-abcd-1523-785feabcd123", 5, 3),
            RecordingConfig(root / "recordings", None, None, 1),
            AlgorithmConfig(False, ()),
            local_ui=LocalUiConfig(True, "127.0.0.1", self.ui_port, root),
        )
        self.server = await create_local_ui_server(self.config)

    async def asyncTearDown(self) -> None:
        self.server.close()
        await self.server.wait_closed()
        self.directory.cleanup()

    async def request(self, path: str, *, method: str = "GET", host: str | None = None) -> bytes:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.ui_port)
        request_host = host or f"127.0.0.1:{self.ui_port}"
        writer.write(f"{method} {path} HTTP/1.1\r\nHost: {request_host}\r\nConnection: close\r\n\r\n".encode("ascii"))
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        return response

    async def test_serves_static_assets_with_loopback_security_headers(self) -> None:
        response = await self.request("/")
        self.assertIn(b"HTTP/1.1 200 OK", response)
        self.assertIn(b"Content-Security-Policy:", response)
        self.assertIn(f"connect-src ws://127.0.0.1:{self.websocket_port}".encode("ascii"), response)
        self.assertIn(b"X-Frame-Options: DENY", response)
        self.assertTrue(response.endswith(b"asset:index.html\n"))

    async def test_runtime_config_uses_the_selected_websocket_endpoint(self) -> None:
        response = await self.request("/runtime-config.js")
        self.assertIn(
            f'window.NEUROBRIDGE_B_CLIENT_ENDPOINT = "ws://127.0.0.1:{self.websocket_port}/neurobridge/v1/ws";'.encode("ascii"),
            response,
        )

    async def test_serves_capture_viewer_from_the_same_gateway(self) -> None:
        page = await self.request("/capture/")
        script = await self.request("/capture/app.js")
        self.assertIn(b"HTTP/1.1 200 OK", page)
        self.assertTrue(page.endswith(b"capture:index.html\n"))
        self.assertTrue(script.endswith(b"capture:app.js\n"))

    async def test_rejects_unapproved_host_and_unknown_paths(self) -> None:
        bad_host = await self.request("/", host="example.com")
        missing = await self.request("/../gateway.toml")
        self.assertIn(b"421 Misdirected Request", bad_host)
        self.assertIn(b"404 Not Found", missing)

    async def test_head_returns_headers_without_a_body(self) -> None:
        response = await self.request("/styles.css", method="HEAD")
        headers, body = response.split(b"\r\n\r\n", 1)
        self.assertIn(b"HTTP/1.1 200 OK", headers)
        self.assertEqual(body, b"")
