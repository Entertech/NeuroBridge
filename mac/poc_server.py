#!/usr/bin/env python3
"""Local-only control page for the macOS Flowtime capture POC."""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
import ipaddress
import json
import logging
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
import threading
import time
from typing import Any
from urllib.parse import urlparse

from neurobridge.ble.flowtime import FlowtimeAdapter
from neurobridge.business.gateway import Gateway
from neurobridge.config import GatewayConfig, load
from neurobridge.northbound.websocket import create_server


LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
B_CLIENT_ROOT = ROOT.parent / "tools" / "b-client-test"


class LogBuffer(logging.Handler):
    """Keep a local, presentation-safe tail of operational log messages."""
    def __init__(self, limit: int = 160) -> None:
        super().__init__()
        self.entries: deque[dict[str, Any]] = deque(maxlen=limit)
        self.entries_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "timestampMs": int(record.created * 1000),
                "level": record.levelname,
                "message": record.getMessage(),
            }
            with self.entries_lock:
                self.entries.append(entry)
        except Exception:
            self.handleError(record)

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        with self.entries_lock:
            return {"entries": list(self.entries)}


class CaptureController:
    def __init__(self, config: GatewayConfig) -> None:
        self.gateway = Gateway(config)
        self.connection_error: str | None = None
        self.packet_log_bucket: int | None = None
        self.packet_log_summary: dict[str, dict[str, Any]] = {}
        self.gateway.window_observer = self.log_algorithm_output
        self.adapter = FlowtimeAdapter(config.ble, self.receive_packet, self.update_status, self.gateway.on_device_ready, self.update_connection_error)
        self.server: Any | None = None
        self.adapter_task: asyncio.Task[None] | None = None
        self.started = False
        self.lock = asyncio.Lock()

    async def update_status(self, name: str, value: object) -> None:
        if name == "connectionState" and value == "connected":
            self.connection_error = None
        await self.gateway.update_status(name, value)

    async def update_connection_error(self, error: str) -> None:
        self.connection_error = error

    async def receive_packet(self, characteristic: str, value: bytes) -> None:
        """Forward a packet while adding a rate-limited, non-complete UI log.

        The POC UI may show that data is arriving, but it must not retain full
        physiological raw bytes in a browser-visible log.
        """
        self._summarize_packet(characteristic, value)
        await self.gateway.receive_packet(characteristic, value)

    def _summarize_packet(self, characteristic: str, value: bytes) -> None:
        bucket = int(time.time() * 1000) // 600
        if self.packet_log_bucket is not None and bucket != self.packet_log_bucket:
            fields = []
            for stream, summary in sorted(self.packet_log_summary.items()):
                fields.append(f"{stream}: {summary['count']} packets × {summary['bytes']} B, preview={summary['preview']}")
            if fields:
                LOG.info("Received headband data (600 ms): %s", "; ".join(fields))
            self.packet_log_summary.clear()
        self.packet_log_bucket = bucket
        summary = self.packet_log_summary.setdefault(characteristic, {"count": 0, "bytes": len(value), "preview": value[:8].hex(" ") or "empty"})
        summary["count"] += 1
        summary["bytes"] = len(value)
        summary["preview"] = value[:8].hex(" ") or "empty"

    async def log_algorithm_output(self, _window: object, algorithm: dict | None, reasons: list[str], valid: bool) -> None:
        """Expose a POC-only summary of bridge output without exposing raw bytes."""
        if algorithm is None:
            suffix = f"; reasons={','.join(reasons)}" if reasons else ""
            LOG.info("Algorithm output: unavailable (valid=%s%s)", valid, suffix)
            return
        safe = {
            str(key): value
            for key, value in algorithm.items()
            if not any(marker in str(key).lower() for marker in ("raw", "base64", "byte"))
        }
        # The SDK includes processed waveform arrays under eeg.wave.  The capture
        # log is intended for operational observability, not retaining a second
        # copy of physiological time-series data in a browser-visible surface.
        if isinstance(safe.get("eeg"), dict):
            safe["eeg"] = {key: value for key, value in safe["eeg"].items() if key != "wave"}
        rendered = json.dumps(safe, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(rendered) > 2048:
            rendered = rendered[:2048] + "…(truncated)"
        LOG.info("Algorithm output (valid=%s): %s", valid, rendered)

    async def start(self) -> dict[str, Any]:
        async with self.lock:
            if self.started:
                return self.snapshot()
            await self.gateway.start()
            self.server = await create_server(self.gateway)
            self.adapter_task = asyncio.create_task(self.adapter.run(), name="flowtime-adapter")
            self.started = True
            LOG.info("Capture requested: scanning for the configured Flowtime headband")
            return self.snapshot()

    async def stop(self) -> dict[str, Any]:
        async with self.lock:
            if not self.started:
                return self.snapshot()
            await self.adapter.stop()
            if self.adapter_task:
                try:
                    await asyncio.wait_for(self.adapter_task, timeout=4)
                except TimeoutError:
                    self.adapter_task.cancel()
                    try:
                        await self.adapter_task
                    except asyncio.CancelledError:
                        pass
            if self.server:
                self.server.close()
                await self.server.wait_closed()
            await self.gateway.stop()
            self.server = None
            self.adapter_task = None
            self.started = False
            LOG.info("Capture stopped")
            return self.snapshot()

    async def export_current_recording(self) -> Path:
        recording_id = self.gateway.store.recording_id or self.gateway.store.last_recording_id
        if not recording_id:
            raise ValueError("No recording is available to save yet.")
        archive = self.gateway.store.export(recording_id)
        LOG.info("Capture archive prepared for download: recordingId=%s", recording_id)
        return archive

    def snapshot(self) -> dict[str, Any]:
        export_recording_id = self.gateway.store.recording_id or self.gateway.store.last_recording_id
        return {
            "captureRunning": self.started,
            "recordingId": self.gateway.store.recording_id,
            "connectionError": self.connection_error,
            "exportRecordingId": export_recording_id,
            "exportUrl": "/api/recordings/current/download" if export_recording_id else None,
            "websocketUrl": f"ws://{self.gateway.config.server.host}:{self.gateway.config.server.port}{self.gateway.config.server.path}",
            **self.gateway.status_result(),
        }


def local_only(address: str) -> str:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as error:
        raise argparse.ArgumentTypeError("UI host must be a loopback IP address") from error
    if not parsed.is_loopback:
        raise argparse.ArgumentTypeError("The macOS POC UI may bind only to loopback")
    return address


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def handler_factory(controller: CaptureController, loop: asyncio.AbstractEventLoop, logs: LogBuffer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NeuroBridgeMacPOC/1.0"

        def log_message(self, fmt: str, *args: object) -> None:
            message = fmt % args
            if "GET /api/status" in message or "GET /api/logs" in message:
                return
            LOG.info("UI %s - %s", self.address_string(), message)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/status":
                self.respond_json(self.run(controller.snapshot))
                return
            if path == "/api/logs":
                self.respond_json(logs.snapshot())
                return
            if path == "/api/recordings/current/download":
                archive = self.run(controller.export_current_recording)
                if isinstance(archive, Path):
                    self.respond_download(archive)
                else:
                    self.respond_json(archive if isinstance(archive, dict) else {"error": "Cannot create capture archive."}, status=HTTPStatus.CONFLICT)
                return
            if path in {"/", "/index.html"}:
                self.respond_file(ROOT / "capture" / "index.html")
                return
            if path.startswith("/capture/"):
                self.respond_file(ROOT / "capture" / path.removeprefix("/capture/"))
                return
            if path == "/b-client" or path == "/b-client/":
                self.respond_file(B_CLIENT_ROOT / "index.html")
                return
            if path == "/b-client/config.js":
                self.respond_javascript("window.NEUROBRIDGE_B_CLIENT_ENDPOINT=" + json.dumps(controller.snapshot()["websocketUrl"]) + ";\n")
                return
            if path.startswith("/b-client/"):
                self.respond_file(B_CLIENT_ROOT / path.removeprefix("/b-client/"))
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/capture/start":
                self.respond_json(self.run(controller.start))
                return
            if path == "/api/capture/stop":
                self.respond_json(self.run(controller.stop))
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def run(self, operation: Any) -> dict[str, Any]:
            try:
                value = operation() if operation is controller.snapshot else operation()
                if asyncio.iscoroutine(value):
                    return asyncio.run_coroutine_threadsafe(value, loop).result(timeout=8)
                return value
            except FutureTimeoutError:
                return {"error": "Operation timed out; inspect the local POC terminal."}
            except Exception as error:
                LOG.exception("POC UI operation failed")
                return {"error": str(error)}

        def respond_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def respond_download(self, archive: Path) -> None:
            try:
                allowed = (controller.gateway.config.recording.directory / "exports").resolve()
                path = archive.resolve(strict=True)
                if not path.is_relative_to(allowed) or path.suffix != ".zip":
                    raise FileNotFoundError
                body = path.read_bytes()
            except (FileNotFoundError, OSError):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def respond_javascript(self, source: str) -> None:
            body = source.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def respond_file(self, requested: Path) -> None:
            try:
                path = requested.resolve(strict=True)
                allowed = (ROOT / "capture").resolve() if path.is_relative_to((ROOT / "capture").resolve()) else B_CLIENT_ROOT.resolve()
                if not path.is_relative_to(allowed) or not path.is_file():
                    raise FileNotFoundError
                body = path.read_bytes()
            except (FileNotFoundError, OSError):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}.get(path.suffix, "application/octet-stream")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


async def run(args: argparse.Namespace) -> None:
    config = load(args.config)
    if not ipaddress.ip_address(config.server.host).is_loopback:
        raise ValueError("The macOS POC WebSocket server must use a loopback host")
    controller = CaptureController(config)
    logs = LogBuffer()
    root_logger = logging.getLogger()
    root_logger.addHandler(logs)
    loop = asyncio.get_running_loop()
    httpd = ThreadingHTTPServer((args.ui_host, args.ui_port), handler_factory(controller, loop, logs))
    http_thread = threading.Thread(target=httpd.serve_forever, name="mac-poc-http", daemon=True)
    http_thread.start()
    LOG.info("Open http://%s:%s/ to start local headband capture", args.ui_host, args.ui_port)
    stopped = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    try:
        await stopped.wait()
    finally:
        httpd.shutdown()
        httpd.server_close()
        await controller.stop()
        root_logger.removeHandler(logs)
        logs.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local macOS headband capture POC UI.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--ui-host", type=local_only, default="127.0.0.1")
    parser.add_argument("--ui-port", type=int, default=8090)
    args = parser.parse_args()
    if not 1 <= args.ui_port <= 65535:
        parser.error("ui-port must be between 1 and 65535")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
