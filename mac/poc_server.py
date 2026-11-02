#!/usr/bin/env python3
"""Local-only control page for the macOS Flowtime capture POC."""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
import ipaddress
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
import threading
from typing import Any
from urllib.parse import urlparse

from neurobridge.capture_control import CaptureControl
from neurobridge.business.gateway import Gateway
from neurobridge.config import GatewayConfig, load
from neurobridge.northbound.websocket import create_server


LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
CAPTURE_WEB_ROOT = PROJECT_ROOT / "web" / "capture"
B_CLIENT_ROOT = PROJECT_ROOT / "web" / "b-client-test"


class CaptureController:
    """Use the same device lifecycle as the deployed gateway."""
    def __init__(self, config: GatewayConfig) -> None:
        self.gateway = Gateway(config)
        self.capture = CaptureControl(self.gateway)
        self.server: Any | None = None
        self.lock = asyncio.Lock()

    async def start(self) -> dict[str, Any]:
        async with self.lock:
            if self.server is None:
                await self.gateway.start()
                self.server = await create_server(self.gateway)
            return await self.capture.start()

    async def stop(self) -> dict[str, Any]:
        return await self.capture.stop()

    async def shutdown(self) -> None:
        try:
            await self.capture.shutdown()
        finally:
            if self.server:
                self.server.close()
                await self.server.wait_closed()
            await self.gateway.stop()

    async def export_current_recording(self) -> Path:
        if self.gateway.store.recording_id:
            raise ValueError("Recording is still active; stop capture before exporting it.")
        recording_id = self.gateway.store.last_recording_id
        if not recording_id:
            raise ValueError("No recording is available to save yet.")
        archive = self.gateway.store.export(recording_id)
        LOG.info("Capture archive prepared for download: recordingId=%s", recording_id)
        return archive

    def snapshot(self) -> dict[str, Any]:
        result = self.capture.snapshot()
        if result["exportUrl"]:
            result["exportUrl"] = "/api/recordings/current/download"
        return result


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


def handler_factory(controller: CaptureController, loop: asyncio.AbstractEventLoop):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NeuroBridgeMacPOC/1.0"

        def origin_allowed(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin == "null" or origin == "http://" + self.headers.get("Host", "")

        def end_headers(self) -> None:
            origin = self.headers.get("Origin")
            if origin and self.origin_allowed():
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            super().end_headers()

        def do_OPTIONS(self) -> None:
            if not self.origin_allowed():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()

        def log_message(self, fmt: str, *args: object) -> None:
            message = fmt % args
            if "GET /api/status" in message or "GET /api/logs" in message:
                return
            LOG.info("UI %s - %s", self.address_string(), message)

        def do_GET(self) -> None:
            if not self.origin_allowed():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            path = urlparse(self.path).path
            if path == "/api/status":
                self.respond_json(self.run(controller.snapshot))
                return
            if path == "/api/logs":
                self.respond_json(controller.capture.logs.snapshot())
                return
            if path == "/api/recordings/current/download":
                archive = self.run(controller.export_current_recording)
                if isinstance(archive, Path):
                    self.respond_download(archive)
                else:
                    self.respond_json(archive if isinstance(archive, dict) else {"error": "Cannot create capture archive."}, status=HTTPStatus.CONFLICT)
                return
            if path in {"/", "/index.html", "/capture"}:
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/capture/index.html")
                self.end_headers()
                return
            if path.startswith("/capture/"):
                self.respond_file(CAPTURE_WEB_ROOT / path.removeprefix("/capture/"))
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
            if path.startswith("/b-client-test/"):
                self.respond_file(B_CLIENT_ROOT / path.removeprefix("/b-client-test/"))
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if not self.origin_allowed():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
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
                    return asyncio.run_coroutine_threadsafe(value, loop).result(timeout=12)
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
                allowed = CAPTURE_WEB_ROOT.resolve() if path.is_relative_to(CAPTURE_WEB_ROOT.resolve()) else B_CLIENT_ROOT.resolve()
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
    loop = asyncio.get_running_loop()
    httpd = ThreadingHTTPServer((args.ui_host, args.ui_port), handler_factory(controller, loop))
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
        await controller.shutdown()


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
