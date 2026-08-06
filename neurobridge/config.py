from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    path: str


@dataclass(frozen=True)
class BleConfig:
    enabled: bool
    device_name: str | None
    model_nbr_uuid: str
    scan_timeout_seconds: int
    reconnect_delay_seconds: int


@dataclass(frozen=True)
class RecordingConfig:
    directory: Path
    subject_id: str | None
    replay_recording_id: str | None
    replay_speed: float


@dataclass(frozen=True)
class AlgorithmConfig:
    enabled: bool
    command: tuple[str, ...]


@dataclass(frozen=True)
class GatewayConfig:
    server: ServerConfig
    ble: BleConfig
    recording: RecordingConfig
    algorithm: AlgorithmConfig


def load(path: str | Path) -> GatewayConfig:
    with Path(path).open("rb") as file:
        raw = tomllib.load(file)
    server, ble, recording, algorithm = (raw.get(name, {}) for name in ("server", "ble", "recording", "algorithm"))
    replay_speed = float(recording.get("replay_speed", 1))
    if replay_speed <= 0:
        raise ValueError("recording.replay_speed must be greater than zero")
    host, port, endpoint = str(server.get("host", "127.0.0.1")), int(server.get("port", 8765)), str(server.get("path", "/neurobridge/v1/ws"))
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("server.host must be a static IP address, not a DNS name or wildcard") from exc
    if not (address.is_private or address.is_loopback) or not 1 <= port <= 65535 or not endpoint.startswith("/"):
        raise ValueError("server host must be private/loopback; port and WebSocket path are invalid")
    return GatewayConfig(
        server=ServerConfig(host, port, endpoint),
        ble=BleConfig(bool(ble.get("enabled", False)), ble.get("device_name") or None, str(ble.get("model_nbr_uuid", "0000ff10-1212-abcd-1523-785feabcd123")).lower(), int(ble.get("scan_timeout_seconds", 5)), int(ble.get("reconnect_delay_seconds", 3))),
        recording=RecordingConfig(Path(recording.get("directory", "./recordings")), recording.get("subject_id") or None, recording.get("replay_recording_id") or None, replay_speed),
        algorithm=AlgorithmConfig(bool(algorithm.get("enabled", False)), tuple(algorithm.get("command", []))),
    )
