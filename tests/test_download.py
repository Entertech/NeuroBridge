from __future__ import annotations

import asyncio
from hashlib import sha256
import json
from pathlib import Path
import socket
import tempfile
import unittest
import zipfile

from neurobridge.config import (
    AlgorithmConfig,
    BleConfig,
    DownloadConfig,
    GatewayConfig,
    LoggingConfig,
    RecordingConfig,
    ServerConfig,
)
from neurobridge.download import create_download_server
from neurobridge.business.gateway import Gateway


def unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def http_get(
    port: int,
    target: str,
    method: str = "GET",
    *,
    host: str | None = None,
    include_host: bool = True,
) -> tuple[int, dict[str, str], bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    request_headers = [f"{method} {target} HTTP/1.1"]
    if include_host:
        request_headers.append(f"Host: {host or f'127.0.0.1:{port}'}")
    request_headers.append("Connection: close")
    writer.write(("\r\n".join(request_headers) + "\r\n\r\n").encode("ascii"))
    await writer.drain()
    raw = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, body = raw.split(b"\r\n\r\n", 1)
    lines = head.decode("iso-8859-1").split("\r\n")
    headers = {key.lower(): value.strip() for key, value in (line.split(":", 1) for line in lines[1:])}
    return int(lines[0].split(" ")[1]), headers, body


class DownloadServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.port = unused_port()
        self.gateway = Gateway(
            GatewayConfig(
                ServerConfig("127.0.0.1", unused_port(), "/neurobridge/v1/ws"),
                BleConfig(False, None, "0000ff10-1212-abcd-1523-785feabcd123", 5, 3),
                RecordingConfig(root / "recordings", "SUBJECT-001", None, 1),
                AlgorithmConfig(False, ()),
                DownloadConfig(True, "127.0.0.1", self.port, "/downloads"),
                LoggingConfig(root / "logs"),
            )
        )
        self.gateway.config.logging.directory.mkdir(parents=True)
        (self.gateway.config.logging.directory / "neurobridge.log").write_text("gateway started\n", encoding="utf-8")
        self.documentation = root / "capture-package.pdf"
        self.documentation.write_bytes(b"%PDF-1.4\n%%EOF\n")
        self.gateway.store._capture_package_pdf = self.documentation
        await self.gateway.update_status("connectionState", "connected")
        self.recording_id = self.gateway.store.recording_id
        assert self.recording_id is not None
        self.gateway.store.save_raw_packet(
            stream="eeg", received_at_ms=1000, window_start_ms=600, window_end_ms=1200, value=b"e" * 14
        )
        await self.gateway.update_status("connectionState", "disconnected")
        self.server = await create_download_server(self.gateway)

    async def asyncTearDown(self) -> None:
        self.server.close()
        await self.server.wait_closed()
        await self.gateway.stop()
        self.directory.cleanup()

    async def test_lists_completed_recordings_and_downloads_the_export(self) -> None:
        status, _headers, body = await http_get(self.port, "/downloads")
        self.assertEqual(status, 200)
        index = json.loads(body)
        self.assertEqual(index["recordings"][0]["recordingId"], self.recording_id)

        status, headers, body = await http_get(self.port, f"/downloads/recordings/{self.recording_id}.zip")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/zip")
        archive = Path(self.directory.name) / "download.zip"
        archive.write_bytes(body)
        with zipfile.ZipFile(archive) as bundle:
            self.assertIn(f"{self.recording_id}/raw/eeg.jsonl", bundle.namelist())
            self.assertIn(f"{self.recording_id}/{self.documentation.name}", bundle.namelist())

    async def test_downloads_a_log_snapshot_and_rejects_path_traversal(self) -> None:
        status, _headers, body = await http_get(self.port, "/downloads/logs/neurobridge-logs.zip")
        self.assertEqual(status, 200)
        archive = Path(self.directory.name) / "logs.zip"
        archive.write_bytes(body)
        with zipfile.ZipFile(archive) as bundle:
            self.assertIn("neurobridge.log", bundle.namelist())
            self.assertIn("manifest.json", bundle.namelist())
            manifest = json.loads(bundle.read("manifest.json"))
            self.assertEqual(manifest["scope"], "application-file-logs-only")
            self.assertIn("systemd journal", manifest["excluded"])
            details = {entry["name"]: entry for entry in manifest["fileDetails"]}
            log_bytes = bundle.read("neurobridge.log")
            self.assertEqual(details["neurobridge.log"]["bytes"], len(log_bytes))
            self.assertEqual(details["neurobridge.log"]["sha256"], sha256(log_bytes).hexdigest())

        status, _headers, _body = await http_get(self.port, "/downloads/recordings/%2E%2E.zip")
        self.assertEqual(status, 404)

    async def test_active_recordings_are_not_exposed(self) -> None:
        await self.gateway.update_status("connectionState", "connected")
        active = self.gateway.store.recording_id
        assert active is not None
        status, _headers, body = await http_get(self.port, "/downloads")
        self.assertNotIn(active, [entry["recordingId"] for entry in json.loads(body)["recordings"]])
        status, _headers, _body = await http_get(self.port, f"/downloads/recordings/{active}.zip")
        self.assertEqual(status, 409)

    async def test_rejects_missing_or_unapproved_host(self) -> None:
        status, _headers, _body = await http_get(self.port, "/downloads", host="attacker.example")
        self.assertEqual(status, 421)
        status, _headers, _body = await http_get(self.port, "/downloads", include_host=False)
        self.assertEqual(status, 421)
