"""Loopback-only configuration UI for an installed Ubuntu gateway.

The B-side protocol intentionally has no administrative operations.  This
small HTTP service is instead bound to the gateway host's loopback interface,
where an operator using the local console can edit the deployment settings
without hand-editing TOML.  It must never be bound to the dedicated B-side
Ethernet address or another network.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable
from urllib.parse import urlparse

from .config import GatewayConfig, load


DEFAULT_CONFIG_PATH = Path("/etc/neurobridge/gateway.toml")
DEFAULT_WEB_ROOT = Path("/opt/neurobridge/web/capture")
DEFAULT_B_CLIENT_ROOT = Path("/opt/neurobridge/web/b-client-test")
MAX_REQUEST_BYTES = 64 * 1024

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def loopback_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("UI host must be a loopback IP address") from error
    if not address.is_loopback:
        raise argparse.ArgumentTypeError("The gateway configuration UI may bind only to loopback")
    return value


def _required_object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _string(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string(value: object, name: str) -> str:
    if value is None:
        return ""
    return _string(value, name, allow_empty=True)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    return float(value)


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value


def _toml_string(value: str) -> str:
    """JSON string syntax is valid TOML basic-string syntax for these fields."""
    return json.dumps(value, ensure_ascii=False)


def _toml_list(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _raw_algorithm_command(raw: dict[str, Any]) -> list[str] | None:
    command = raw.get("algorithm", {}).get("command") if isinstance(raw.get("algorithm"), dict) else None
    if command is None:
        return None
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("algorithm.command in the existing configuration is invalid")
    return command


def config_to_api(config: GatewayConfig) -> dict[str, Any]:
    """Return only editable deployment fields; no process environment or secrets."""
    value = asdict(config)
    value["recording"]["directory"] = str(config.recording.directory)
    value["logging"]["directory"] = str(config.logging.directory)
    value["algorithm"].pop("command", None)
    return value


def document_from_api(payload: object, raw_current: dict[str, Any]) -> str:
    """Validate the editable schema and render deterministic TOML.

    The native bridge command is intentionally not editable in the browser. If
    an administrator has a controlled override, it is preserved verbatim.
    """
    root = _required_object(payload, "config")
    expected = {"server", "network", "ble", "recording", "download", "logging", "algorithm"}
    missing = expected - root.keys()
    if missing:
        raise ValueError("missing configuration sections: " + ", ".join(sorted(missing)))

    server = _required_object(root["server"], "server")
    network = _required_object(root["network"], "network")
    ble = _required_object(root["ble"], "ble")
    recording = _required_object(root["recording"], "recording")
    download = _required_object(root["download"], "download")
    logging = _required_object(root["logging"], "logging")
    algorithm = _required_object(root["algorithm"], "algorithm")

    values = {
        "server.host": _string(server.get("host"), "server.host"),
        "server.port": _integer(server.get("port"), "server.port"),
        "server.path": _string(server.get("path"), "server.path"),
        "network.mode": _string(network.get("mode"), "network.mode"),
        "network.interface": _optional_string(network.get("interface"), "network.interface"),
        "network.subnet_cidr": _optional_string(network.get("subnet_cidr"), "network.subnet_cidr"),
        "network.dhcp_range_start": _optional_string(network.get("dhcp_range_start"), "network.dhcp_range_start"),
        "network.dhcp_range_end": _optional_string(network.get("dhcp_range_end"), "network.dhcp_range_end"),
        "network.dhcp_lease_time": _string(network.get("dhcp_lease_time"), "network.dhcp_lease_time"),
        "ble.enabled": _boolean(ble.get("enabled"), "ble.enabled"),
        "ble.device_name": _optional_string(ble.get("device_name"), "ble.device_name"),
        "ble.model_nbr_uuid": _string(ble.get("model_nbr_uuid"), "ble.model_nbr_uuid"),
        "ble.scan_timeout_seconds": _integer(ble.get("scan_timeout_seconds"), "ble.scan_timeout_seconds"),
        "ble.reconnect_delay_seconds": _integer(ble.get("reconnect_delay_seconds"), "ble.reconnect_delay_seconds"),
        "recording.directory": _string(recording.get("directory"), "recording.directory"),
        "recording.subject_id": _optional_string(recording.get("subject_id"), "recording.subject_id"),
        "recording.replay_recording_id": _optional_string(recording.get("replay_recording_id"), "recording.replay_recording_id"),
        "recording.replay_speed": _number(recording.get("replay_speed"), "recording.replay_speed"),
        "download.enabled": _boolean(download.get("enabled"), "download.enabled"),
        "download.host": _string(download.get("host"), "download.host"),
        "download.port": _integer(download.get("port"), "download.port"),
        "download.path": _string(download.get("path"), "download.path"),
        "logging.directory": _string(logging.get("directory"), "logging.directory"),
        "logging.filename": _string(logging.get("filename"), "logging.filename"),
        "logging.level": _string(logging.get("level"), "logging.level"),
        "algorithm.enabled": _boolean(algorithm.get("enabled"), "algorithm.enabled"),
    }
    command = _raw_algorithm_command(raw_current)
    lines = [
        "# Managed through the local NeuroBridge configuration console.",
        "# Keep this file on the gateway host; it must not be committed or copied to an untrusted system.",
        "[server]",
        f"host = {_toml_string(values['server.host'])}",
        f"port = {values['server.port']}",
        f"path = {_toml_string(values['server.path'])}",
        "",
        "[network]",
        f"mode = {_toml_string(values['network.mode'])}",
        f"interface = {_toml_string(values['network.interface'])}",
        f"subnet_cidr = {_toml_string(values['network.subnet_cidr'])}",
        f"dhcp_range_start = {_toml_string(values['network.dhcp_range_start'])}",
        f"dhcp_range_end = {_toml_string(values['network.dhcp_range_end'])}",
        f"dhcp_lease_time = {_toml_string(values['network.dhcp_lease_time'])}",
        "",
        "[ble]",
        f"enabled = {str(values['ble.enabled']).lower()}",
        f"device_name = {_toml_string(values['ble.device_name'])}",
        f"model_nbr_uuid = {_toml_string(values['ble.model_nbr_uuid'])}",
        f"scan_timeout_seconds = {values['ble.scan_timeout_seconds']}",
        f"reconnect_delay_seconds = {values['ble.reconnect_delay_seconds']}",
        "",
        "[recording]",
        f"directory = {_toml_string(values['recording.directory'])}",
        f"subject_id = {_toml_string(values['recording.subject_id'])}",
        f"replay_recording_id = {_toml_string(values['recording.replay_recording_id'])}",
        f"replay_speed = {values['recording.replay_speed']}",
        "",
        "[download]",
        f"enabled = {str(values['download.enabled']).lower()}",
        f"host = {_toml_string(values['download.host'])}",
        f"port = {values['download.port']}",
        f"path = {_toml_string(values['download.path'])}",
        "",
        "[logging]",
        f"directory = {_toml_string(values['logging.directory'])}",
        f"filename = {_toml_string(values['logging.filename'])}",
        f"level = {_toml_string(values['logging.level'])}",
        "",
        "[algorithm]",
        f"enabled = {str(values['algorithm.enabled']).lower()}",
    ]
    if command is not None:
        lines.append(f"command = {_toml_list(command)}")
    return "\n".join(lines) + "\n"


def _network_fields(config: dict[str, Any]) -> tuple[object, ...]:
    return (
        config["server"]["host"],
        config["network"]["mode"],
        config["network"]["interface"],
        config["network"]["subnet_cidr"],
        config["network"]["dhcp_range_start"],
        config["network"]["dhcp_range_end"],
        config["network"]["dhcp_lease_time"],
    )


class ConfigUiController:
    def __init__(
        self,
        config_path: Path,
        *,
        command_runner: CommandRunner = subprocess.run,
        network_command: str = "neurobridge-network-config",
    ) -> None:
        self.config_path = config_path
        self.command_runner = command_runner
        self.network_command = network_command

    def _raw(self) -> dict[str, Any]:
        import tomllib

        with self.config_path.open("rb") as file:
            return tomllib.load(file)

    def snapshot(self) -> dict[str, Any]:
        return {"config": config_to_api(load(self.config_path)), "configPath": str(self.config_path)}

    def service_status(self) -> dict[str, Any]:
        result = self.command_runner(
            ["systemctl", "is-active", "neurobridge.service"], capture_output=True, text=True, check=False
        )
        state = result.stdout.strip() or "unknown"
        return {
            "pageMode": "gateway",
            "captureRunning": state == "active",
            "connectionState": "gateway-running" if state == "active" else "gateway-stopped",
            "algorithmState": "managed-by-gateway",
            "recordingId": None,
            "connectionError": "" if state == "active" else f"Gateway service is {state}.",
            "websocketUrl": None,
        }

    def service_logs(self) -> dict[str, list[dict[str, Any]]]:
        result = self.command_runner(
            ["journalctl", "-u", "neurobridge.service", "-n", "160", "--no-pager", "--output=json"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Could not read gateway service logs.")
        entries: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            try:
                row = json.loads(line)
                message = str(row.get("MESSAGE", ""))
                if not message:
                    continue
                timestamp = int(str(row.get("_SOURCE_REALTIME_TIMESTAMP", "0"))) // 1000
                priority = str(row.get("PRIORITY", "6"))
                level = {"0": "CRITICAL", "1": "CRITICAL", "2": "CRITICAL", "3": "ERROR", "4": "WARNING", "5": "INFO"}.get(priority, "INFO")
                entries.append({"timestampMs": timestamp, "level": level, "message": message[:4096]})
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
        return {"entries": entries}

    def control_capture(self, action: str) -> dict[str, Any]:
        if action not in {"start", "stop"}:
            raise ValueError("unsupported gateway control action")
        result = self.command_runner(
            ["systemctl", action, "neurobridge.service"], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Could not {action} the gateway service.")
        return self.service_status()

    def _atomic_write(self, document: str) -> None:
        existing = self.config_path.stat()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.config_path.parent, delete=False) as file:
            file.write(document)
            temporary_path = Path(file.name)
        try:
            temporary_path.chmod(0o640)
            os.chown(temporary_path, existing.st_uid, existing.st_gid)
            temporary_path.replace(self.config_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def save(self, payload: object) -> dict[str, Any]:
        request = _required_object(payload, "request")
        raw = self._raw()
        current = config_to_api(load(self.config_path))
        document = document_from_api(request.get("config"), raw)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.config_path.parent, delete=False) as temporary:
            temporary.write(document)
            temporary_path = Path(temporary.name)
        try:
            candidate = config_to_api(load(temporary_path))
        finally:
            temporary_path.unlink(missing_ok=True)

        apply_network = _boolean(request.get("applyNetwork", False), "applyNetwork")
        if _network_fields(candidate) != _network_fields(current) and not apply_network:
            raise ValueError("Network address, mode, or interface changed. Select ‘apply dedicated-link configuration’ before saving.")

        previous = self.config_path.read_bytes()
        self._atomic_write(document)
        if apply_network:
            result = self.command_runner(
                [self.network_command, "--config", str(self.config_path), "--apply"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                self._atomic_write(previous.decode("utf-8"))
                detail = (result.stderr or result.stdout).strip()
                raise RuntimeError(detail or "Dedicated-link configuration was rejected; gateway.toml was restored.")

        for unit in ("neurobridge-dhcp.service", "neurobridge.service"):
            result = self.command_runner(["systemctl", "restart", unit], capture_output=True, text=True, check=False)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise RuntimeError(f"Configuration was saved, but {unit} could not restart: {detail or 'unknown error'}")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return {"message": f"Configuration saved and gateway restarted at {timestamp}.", **self.snapshot()}


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def handler_factory(
    controller: ConfigUiController,
    *,
    web_root: Path,
    b_client_root: Path,
    expected_origin: str,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "NeuroBridgeConfigUI/1.0"

        def log_message(self, _fmt: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            try:
                if path == "/api/config":
                    self.respond_json(controller.snapshot())
                elif path == "/api/status":
                    self.respond_json(controller.service_status())
                elif path == "/api/logs":
                    self.respond_json(controller.service_logs())
                elif path in {"/", "/index.html"}:
                    self.respond_file(web_root / "index.html", web_root)
                elif path.startswith("/capture/"):
                    self.respond_file(web_root / path.removeprefix("/capture/"), web_root)
                elif path in {"/b-client", "/b-client/"}:
                    self.respond_file(b_client_root / "index.html", b_client_root)
                elif path.startswith("/b-client/"):
                    self.respond_file(b_client_root / path.removeprefix("/b-client/"), b_client_root)
                else:
                    self.respond_error(HTTPStatus.NOT_FOUND, "Not found.")
            except Exception as error:
                self.respond_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))

        def do_POST(self) -> None:
            if self.headers.get("Origin") != expected_origin:
                self.respond_error(HTTPStatus.FORBIDDEN, "Requests must originate from the local configuration console.")
                return
            path = urlparse(self.path).path
            try:
                if path == "/api/capture/start":
                    self.respond_json(controller.control_capture("start"))
                elif path == "/api/capture/stop":
                    self.respond_json(controller.control_capture("stop"))
                elif path == "/api/config":
                    self.respond_json(controller.save(self.request_json()))
                else:
                    self.respond_error(HTTPStatus.NOT_FOUND, "Not found.")
            except ValueError as error:
                self.respond_error(HTTPStatus.BAD_REQUEST, str(error))
            except Exception as error:
                self.respond_error(HTTPStatus.CONFLICT, str(error))

        def request_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("Configuration request must be a JSON document smaller than 64 KiB.")
            try:
                return _required_object(json.loads(self.rfile.read(length)), "request")
            except json.JSONDecodeError as error:
                raise ValueError("Configuration request is not valid JSON.") from error

        def respond_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def respond_error(self, status: HTTPStatus, message: str) -> None:
            self.respond_json({"error": message[:4096]}, status)

        def respond_file(self, requested: Path, allowed: Path) -> None:
            try:
                path = requested.resolve(strict=True)
                allowed_path = allowed.resolve(strict=True)
                if not path.is_relative_to(allowed_path) or not path.is_file():
                    raise FileNotFoundError
                body = path.read_bytes()
            except (FileNotFoundError, OSError):
                self.respond_error(HTTPStatus.NOT_FOUND, "Static file not found.")
                return
            mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}.get(path.suffix, "application/octet-stream")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the loopback-only NeuroBridge gateway configuration UI.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--web-root", type=Path, default=DEFAULT_WEB_ROOT)
    parser.add_argument("--b-client-root", type=Path, default=DEFAULT_B_CLIENT_ROOT)
    parser.add_argument("--host", type=loopback_address, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not args.config.is_file():
        parser.error(f"configuration file does not exist: {args.config}")
    if not args.web_root.is_dir() or not args.b_client_root.is_dir():
        parser.error("static web directories do not exist")
    controller = ConfigUiController(args.config)
    server = ThreadingHTTPServer(
        (args.host, args.port),
        handler_factory(controller, web_root=args.web_root, b_client_root=args.b_client_root, expected_origin=f"http://{args.host}:{args.port}"),
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":  # pragma: no cover - console entry point
    main()
