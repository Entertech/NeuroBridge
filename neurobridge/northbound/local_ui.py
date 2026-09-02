"""Minimal loopback-only HTTP server for the bundled browser console."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ..config import GatewayConfig
from .strategy import access_strategy

LOG = logging.getLogger(__name__)
MAX_REQUEST_HEADER_BYTES = 16 * 1024
ASSETS = {
    "/": ("b-client-test/index.html", "text/html; charset=utf-8"),
    "/index.html": ("b-client-test/index.html", "text/html; charset=utf-8"),
    "/app.js": ("b-client-test/app.js", "text/javascript; charset=utf-8"),
    "/version.js": ("b-client-test/version.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("b-client-test/styles.css", "text/css; charset=utf-8"),
    "/capture": ("capture/index.html", "text/html; charset=utf-8"),
    "/capture/": ("capture/index.html", "text/html; charset=utf-8"),
    "/capture/index.html": ("capture/index.html", "text/html; charset=utf-8"),
    "/capture/app.js": ("capture/app.js", "text/javascript; charset=utf-8"),
    "/capture/styles.css": ("capture/styles.css", "text/css; charset=utf-8"),
}


def _asset_path(directory: Path, relative_name: str) -> Path:
    """Resolve bundled assets while retaining the old custom-directory layout."""

    direct = directory / relative_name
    if direct.is_file():
        return direct
    group, filename = relative_name.split("/", 1)
    if group == "b-client-test":
        legacy = directory / filename
        if legacy.is_file():
            return legacy
    elif group == "capture":
        sibling = directory.parent / "capture" / filename
        if sibling.is_file():
            return sibling
    return direct


def resolve_ui_directory(config: GatewayConfig) -> Path:
    candidates = []
    if config.local_ui.directory is not None:
        candidates.append(config.local_ui.directory)
    candidates.extend(
        (
            Path(__file__).resolve().parents[2] / "web",
            Path("/opt/neurobridge/web"),
            Path("/opt/neurobridge/web/b-client-test"),
        )
    )
    for candidate in candidates:
        primary_assets = ("b-client-test/index.html", "b-client-test/app.js", "b-client-test/version.js", "b-client-test/styles.css")
        if all(_asset_path(candidate, name).is_file() for name in primary_assets):
            return candidate
    raise FileNotFoundError("Local browser UI assets were not found; configure local_ui.directory")


def _response(config: GatewayConfig, status: int, reason: str, content_type: str, body: bytes) -> bytes:
    websocket_origin = f"ws://{config.server.host}:{config.server.port}"
    headers = (
        f"HTTP/1.1 {status} {reason}",
        "Connection: close",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "Cache-Control: no-store",
        "X-Content-Type-Options: nosniff",
        "X-Frame-Options: DENY",
        "Referrer-Policy: no-referrer",
        f"Content-Security-Policy: default-src 'self'; connect-src {websocket_origin}; "
        "script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'",
    )
    return ("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body


def _runtime_config(config: GatewayConfig) -> bytes:
    endpoint = f"ws://{config.server.host}:{config.server.port}{config.server.path}"
    return (
        "window.NEUROBRIDGE_B_CLIENT_ENDPOINT = "
        + json.dumps(endpoint, ensure_ascii=True)
        + ";\n"
    ).encode("ascii")


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    config: GatewayConfig,
    ui_directory: Path,
) -> None:
    peer = writer.get_extra_info("peername")
    try:
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            writer.write(_response(config, 400, "Bad Request", "text/plain; charset=utf-8", b"Invalid request\n"))
            await writer.drain()
            return
        if len(raw) > MAX_REQUEST_HEADER_BYTES:
            writer.write(_response(config, 431, "Request Header Fields Too Large", "text/plain; charset=utf-8", b"Request header too large\n"))
            await writer.drain()
            return
        lines = raw.decode("iso-8859-1").split("\r\n")
        request = lines[0].split(" ")
        if len(request) != 3:
            writer.write(_response(config, 400, "Bad Request", "text/plain; charset=utf-8", b"Invalid request\n"))
            await writer.drain()
            return
        method, target, _version = request
        if method not in {"GET", "HEAD"}:
            writer.write(_response(config, 405, "Method Not Allowed", "text/plain; charset=utf-8", b"Method not allowed\n"))
            await writer.drain()
            return
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        expected_host = f"{config.local_ui.host}:{config.local_ui.port}"
        if headers.get("host") != expected_host:
            writer.write(_response(config, 421, "Misdirected Request", "text/plain; charset=utf-8", b"Invalid Host header\n"))
            await writer.drain()
            return
        parsed = urlsplit(target)
        if parsed.query or parsed.fragment:
            writer.write(_response(config, 400, "Bad Request", "text/plain; charset=utf-8", b"Query parameters are not supported\n"))
            await writer.drain()
            return
        path = unquote(parsed.path)
        if path == "/runtime-config.js":
            body = _runtime_config(config)
            content_type = "text/javascript; charset=utf-8"
        elif path in ASSETS:
            filename, content_type = ASSETS[path]
            asset = _asset_path(ui_directory, filename)
            if not asset.is_file():
                body = b"Not found\n"
                response = _response(config, 404, "Not Found", "text/plain; charset=utf-8", body)
                writer.write(response if method == "GET" else response[:-len(body)])
                await writer.drain()
                return
            body = asset.read_bytes()
        else:
            body = b"Not found\n"
            response = _response(config, 404, "Not Found", "text/plain; charset=utf-8", body)
            writer.write(response if method == "GET" else response[:-len(body)])
            await writer.drain()
            return
        response = _response(config, 200, "OK", content_type, body)
        writer.write(response if method == "GET" else response[:-len(body)])
        await writer.drain()
        LOG.debug("Local UI asset served: peer=%s method=%s path=%s bytes=%s", peer, method, path, len(body))
    except (BrokenPipeError, ConnectionResetError):
        LOG.info("Local UI client disconnected before response completed: peer=%s", peer)
    except Exception:
        LOG.exception("Local UI request failed: peer=%s", peer)
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()


async def create_local_ui_server(config: GatewayConfig) -> asyncio.Server:
    strategy = access_strategy(config.access.mode)
    strategy.validate(config)
    if not strategy.serves_local_ui(config):
        raise ValueError("The selected access strategy does not serve a local UI")
    ui_directory = resolve_ui_directory(config)
    return await asyncio.start_server(
        lambda reader, writer: _handle_client(reader, writer, config, ui_directory),
        config.local_ui.host,
        config.local_ui.port,
        limit=MAX_REQUEST_HEADER_BYTES,
    )


async def serve_local_ui(config: GatewayConfig) -> None:
    server = await create_local_ui_server(config)
    try:
        LOG.info("Local browser UI listening on http://%s:%s/", config.local_ui.host, config.local_ui.port)
        await asyncio.Future()
    finally:
        server.close()
        await server.wait_closed()
