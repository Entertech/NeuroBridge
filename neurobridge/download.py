"""Strategy-bound HTTP service for recording and operational-log downloads."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from pathlib import Path
import tempfile
from urllib.parse import unquote, urlsplit
import zipfile

from .application.gateway import GatewayApplication as Gateway
from .versioning import APPLICATION_VERSION
from .diagnostics import runtime_context
from .adapters.northbound.protocol import envelope
from .capture_control import CaptureError

LOG = logging.getLogger(__name__)
MAX_REQUEST_HEADER_BYTES = 16 * 1024
FILE_CHUNK_BYTES = 64 * 1024
DEPLOYED_WEB_ROOT = Path("/opt/neurobridge/web")


def _http_response(status: int, reason: str, *, content_type: str, content_length: int, filename: str | None = None, extra_headers: dict | None = None) -> bytes:
    headers = [
        f"HTTP/1.1 {status} {reason}",
        "Connection: close",
        f"Content-Type: {content_type}",
        f"Content-Length: {content_length}",
        "X-Content-Type-Options: nosniff",
    ]
    if filename is not None:
        headers.append(f'Content-Disposition: attachment; filename="{filename}"')
    headers.extend(f"{key}: {value}" for key, value in (extra_headers or {}).items())
    return ("\r\n".join(headers) + "\r\n\r\n").encode("ascii")


def _log_archive(log_directory: Path, filename: str) -> Path:
    """Create a stable snapshot without exposing paths outside the log directory."""
    log_directory.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=".neurobridge-logs-", suffix=".zip", dir=log_directory, delete=False)
    handle.close()
    archive = Path(handle.name)
    candidates = sorted(path for path in log_directory.glob("*.log*") if path.is_file() and not path.is_symlink())
    try:
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("diagnostic-context.json", json.dumps(runtime_context(), ensure_ascii=False, indent=2) + "\n")
            file_details = []
            for path in candidates:
                initial_stat = path.stat()
                digest = sha256()
                size = 0
                info = zipfile.ZipInfo.from_file(path, arcname=path.name)
                info.compress_type = zipfile.ZIP_DEFLATED
                with path.open("rb") as source, bundle.open(info, "w", force_zip64=True) as destination:
                    while chunk := source.read(FILE_CHUNK_BYTES):
                        destination.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                file_details.append(
                    {
                        "name": path.name,
                        "bytes": size,
                        "modifiedAt": datetime.fromtimestamp(initial_stat.st_mtime, timezone.utc).isoformat(),
                        "sha256": digest.hexdigest(),
                    }
                )
            bundle.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "generatedAt": datetime.now(timezone.utc).isoformat(),
                        "applicationVersion": APPLICATION_VERSION,
                        "scope": "application-file-logs-only",
                        "primaryLog": filename,
                        "files": [path.name for path in candidates],
                        "fileDetails": file_details,
                        "excluded": [
                            "systemd journal",
                            "kernel and USB/TTY diagnostics",
                            "gateway configuration contents",
                            "recordings and raw device data",
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
            )
        return archive
    except Exception:
        archive.unlink(missing_ok=True)
        raise


async def _send_file(writer: asyncio.StreamWriter, method: str, path: Path, filename: str) -> None:
    size = path.stat().st_size
    writer.write(_http_response(200, "OK", content_type="application/zip", content_length=size, filename=filename))
    await writer.drain()
    if method == "HEAD":
        return
    with path.open("rb") as file:
        while chunk := file.read(FILE_CHUNK_BYTES):
            writer.write(chunk)
            await writer.drain()


async def _send_error(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
    body = (json.dumps({"code": status, "message": reason}, ensure_ascii=False) + "\n").encode("utf-8")
    writer.write(_http_response(status, reason, content_type="application/json; charset=utf-8", content_length=len(body)))
    writer.write(body)
    await writer.drain()


async def _send_index(writer: asyncio.StreamWriter, gateway: Gateway, method: str) -> None:
    payload = json.dumps(
        {
            "recordings": gateway.store.completed_recordings(),
            "recordingDownloadTemplate": f"{gateway.config.download.path}/recordings/{{recordingId}}.zip",
            "logDownload": f"{gateway.config.download.path}/logs/neurobridge-logs.zip",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    writer.write(_http_response(200, "OK", content_type="application/json; charset=utf-8", content_length=len(payload)))
    if method == "GET":
        writer.write(payload)
    await writer.drain()


async def _send_control_json(writer, status: int, data: dict, message: str, headers: dict) -> None:
    body = (json.dumps(envelope(status, data, message), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    writer.write(_http_response(status, "OK" if status == 200 else "Capture request failed", content_type="application/json; charset=utf-8", content_length=len(body), extra_headers={"Cache-Control": "no-store", **headers}))
    writer.write(body)
    await writer.drain()


async def _handle_control(writer, gateway: Gateway, capture, method: str, path: str, request_headers: dict) -> None:
    headers = {"Vary": "Origin"}
    if not gateway.config.download.capture_control_enabled:
        await _send_control_json(writer, 404, {}, "Capture control is disabled.", headers)
        return
    origin = request_headers.get("origin")
    host, port = gateway.config.download.host, gateway.config.download.port
    authority = f"[{host}]" if ":" in host else host
    own_origin = f"http://{authority}" + (f":{port}" if port != 80 else "")
    allowed = origin is None or origin == own_origin or origin in gateway.config.download.capture_allowed_origins
    if not allowed:
        LOG.warning("Capture HTTP origin rejected: bootId=%s peer=%s", gateway.boot_id, writer.get_extra_info("peername"))
        await _send_control_json(writer, 403, {}, "Browser origin is not allowed.", headers)
        return
    if origin is not None:
        headers["Access-Control-Allow-Origin"] = origin
    routes = {"/api/status": "GET", "/api/logs": "GET", "/api/capture/start": "POST", "/api/capture/stop": "POST"}
    if path not in routes:
        await _send_control_json(writer, 404, {}, "Unknown capture endpoint.", headers)
        return
    if method == "OPTIONS":
        headers.update({"Access-Control-Allow-Methods": routes[path] + ", OPTIONS", "Access-Control-Allow-Headers": "Content-Type"})
        if request_headers.get("access-control-request-private-network") == "true":
            headers["Access-Control-Allow-Private-Network"] = "true"
        writer.write(_http_response(204, "No Content", content_type="text/plain", content_length=0, extra_headers=headers))
        await writer.drain()
        return
    if method != routes[path]:
        await _send_control_json(writer, 405, {}, "Method not allowed.", headers)
        return
    if request_headers.get("content-length", "0") != "0" or "transfer-encoding" in request_headers:
        await _send_control_json(writer, 400, {}, "Capture requests must not have a body.", headers)
        return
    if capture is None:
        await _send_control_json(writer, 503, {}, "Capture control service is unavailable.", headers)
        return
    try:
        if path == "/api/status":
            data = capture.snapshot()
        elif path == "/api/logs":
            data = capture.logs.snapshot()
        else:
            LOG.info("Capture HTTP operation requested: bootId=%s action=%s peer=%s", gateway.boot_id, path.rsplit("/", 1)[-1], writer.get_extra_info("peername"))
            data = await (capture.start() if path.endswith("/start") else capture.stop())
    except CaptureError as exc:
        status, data, message = exc.code, {}, str(exc)
    except Exception:
        LOG.exception("Capture HTTP operation failed: bootId=%s path=%s", gateway.boot_id, path)
        status, data, message = 500, {}, "Capture operation failed; inspect gateway logs."
    else:
        status, message = 200, "OK"
    # A transport failure must not be mistaken for a capture operation failure.
    await _send_control_json(writer, status, data, message, headers)


async def _send_static_page(writer, gateway: Gateway, method: str, path: str) -> bool:
    if not gateway.config.download.capture_control_enabled:
        return False
    if path in {"/", "/capture", "/capture/", "/b-client-test/"}:
        location = "/b-client-test/index.html" if path == "/b-client-test/" else "/capture/index.html"
        writer.write(_http_response(302, "Found", content_type="text/plain", content_length=0, extra_headers={"Location": location}))
        await writer.drain()
        return True
    capture_files = {"index.html", "app.js", "styles.css", "README.md"}
    client_files = {"index.html", "app.js", "styles.css", "version.js", "README.md", "configure-wired-ip.bat", "configure-wired-ip.ps1"}
    parts = path.strip("/").split("/")
    if len(parts) != 2 or not ((parts[0] == "capture" and parts[1] in capture_files) or (parts[0] == "b-client-test" and parts[1] in client_files)):
        return False
    web_root = Path(__file__).resolve().parents[1] / "web" / "ubuntu"
    if not web_root.is_dir():
        web_root = DEPLOYED_WEB_ROOT / "ubuntu"
    file = web_root.joinpath(*parts)
    if parts[0] == "b-client-test" and parts[1] in {"version.js", "configure-wired-ip.bat", "configure-wired-ip.ps1"}:
        file = web_root.parent.joinpath(*parts)
    if not file.is_file() or file.is_symlink():
        return False
    body = file.read_bytes()
    content_type = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".md": "text/plain; charset=utf-8"}.get(file.suffix, "application/octet-stream")
    writer.write(_http_response(200, "OK", content_type=content_type, content_length=len(body), extra_headers={"Cache-Control": "no-store"}))
    if method == "GET":
        writer.write(body)
    await writer.drain()
    return True


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, gateway: Gateway, capture=None) -> None:
    temporary: Path | None = None
    peer = writer.get_extra_info("peername")
    try:
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            await _send_error(writer, 400, "Invalid request")
            return
        if len(raw) > MAX_REQUEST_HEADER_BYTES:
            await _send_error(writer, 431, "Request header too large")
            return
        lines = raw.decode("iso-8859-1").split("\r\n")
        request = lines[0].split(" ")
        if len(request) != 3:
            await _send_error(writer, 400, "Invalid request")
            return
        request_headers = {}
        host_headers = []
        for line in lines[1:]:
            if not line:
                continue
            if ":" not in line:
                await _send_error(writer, 400, "Invalid request header")
                return
            name, value = line.split(":", 1)
            if name.lower() in request_headers:
                await _send_error(writer, 400, "Duplicate request header")
                return
            request_headers[name.lower()] = value.strip()
            if name.lower() == "host":
                host_headers.append(value.strip())
        expected_host = f"{gateway.config.download.host}:{gateway.config.download.port}"
        if host_headers != [expected_host]:
            await _send_error(writer, 421, "Misdirected Request")
            return
        method, target, _version = request
        parsed = urlsplit(target)
        path = unquote(parsed.path)
        if method in {"GET", "HEAD"} and not parsed.fragment and await _send_static_page(writer, gateway, method, path):
            return
        if (parsed.query or parsed.fragment) and path.startswith("/api/"):
            await _send_control_json(writer, 400, {}, "Query parameters are not supported.", {})
            return
        if parsed.query or parsed.fragment:
            await _send_error(writer, 400, "Query parameters are not supported")
            return
        if path.startswith("/api/"):
            await _handle_control(writer, gateway, capture, method, path, request_headers)
            return
        if method not in {"GET", "HEAD"}:
            await _send_error(writer, 405, "Method not allowed")
            return
        log_path = "".join(character if character.isprintable() else " " for character in path)[:256]
        LOG.info("Download request received: peer=%s method=%s path=%s", peer, method, log_path)
        base = gateway.config.download.path
        if path in {base, base + "/"}:
            await _send_index(writer, gateway, method)
            return
        recording_prefix = base + "/recordings/"
        if path.startswith(recording_prefix) and path.endswith(".zip"):
            recording_id = path[len(recording_prefix):-4]
            if "/" in recording_id or not gateway.store._safe_recording_id(recording_id):
                await _send_error(writer, 404, "Recording not found")
                return
            if recording_id == gateway.store.recording_id:
                await _send_error(writer, 409, "Recording is still active and cannot be exported")
                return
            try:
                archive = await asyncio.to_thread(gateway.store.export, recording_id)
            except FileNotFoundError:
                await _send_error(writer, 404, "Recording not found")
                return
            except RuntimeError as error:
                await _send_error(writer, 409, str(error))
                return
            await _send_file(writer, method, archive, archive.name)
            LOG.info(
                "Completed recording download: peer=%s recordingId=%s bytes=%s method=%s",
                peer,
                recording_id,
                archive.stat().st_size,
                method,
            )
            return
        if path == base + "/logs/neurobridge-logs.zip":
            temporary = await asyncio.to_thread(_log_archive, gateway.config.logging.directory, gateway.config.logging.filename)
            await _send_file(writer, method, temporary, "neurobridge-logs.zip")
            LOG.info(
                "Completed operational log download: peer=%s bytes=%s method=%s",
                peer,
                temporary.stat().st_size,
                method,
            )
            return
        await _send_error(writer, 404, "Not found")
    except (BrokenPipeError, ConnectionResetError):
        LOG.info("Download client disconnected before transfer completed: peer=%s", peer)
    except Exception:
        LOG.exception("Download service request failed: peer=%s", peer)
        with suppress(Exception):
            await _send_error(writer, 500, "Internal server error")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()


async def create_download_server(gateway: Gateway, capture=None) -> asyncio.Server:
    config = gateway.config.download
    if not config.enabled:
        raise ValueError("Download service is disabled")
    return await asyncio.start_server(
        lambda reader, writer: _handle_client(reader, writer, gateway, capture),
        config.host,
        config.port,
        limit=MAX_REQUEST_HEADER_BYTES,
    )


async def serve_downloads(gateway: Gateway, capture=None) -> None:
    server = await create_download_server(gateway, capture)
    try:
        config = gateway.config.download
        LOG.info("Download service listening on http://%s:%s%s", config.host, config.port, config.path)
        await asyncio.Future()
    finally:
        server.close()
        await server.wait_closed()
