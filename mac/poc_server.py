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

from neurobridge.ble.flowtime import FlowtimeAdapter
from neurobridge.business.gateway import Gateway
from neurobridge.config import GatewayConfig, load
from neurobridge.northbound.websocket import create_server


LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
B_CLIENT_ROOT = ROOT.parent / "tools" / "b-client-test"


class CaptureController:
    def __init__(self, config: GatewayConfig) -> None:
        self.gateway = Gateway(config)
        self.adapter = FlowtimeAdapter(config.ble, self.gateway.receive_packet, self.gateway.update_status, self.gateway.on_device_ready)
        self.server: Any | None = None
        self.adapter_task: asyncio.Task[None] | None = None
        self.started = False
        self.lock = asyncio.Lock()

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

    def snapshot(self) -> dict[str, Any]:
        return {
            "captureRunning": self.started,
            "recordingId": self.gateway.store.recording_id,
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


def handler_factory(controller: CaptureController, loop: asyncio.AbstractEventLoop):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NeuroBridgeMacPOC/1.0"

        def log_message(self, fmt: str, *args: object) -> None:
            LOG.info("UI %s - %s", self.address_string(), fmt % args)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/status":
                self.respond_json(self.run(controller.snapshot))
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

        def respond_json(self, payload: dict[str, Any]) -> None:
            body = json_bytes(payload)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
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
        await controller.stop()


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
