from __future__ import annotations

from dataclasses import dataclass, field
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
class DownloadConfig:
    """HTTP endpoint used only on the dedicated deployment network."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8766
    path: str = "/downloads"


@dataclass(frozen=True)
class LoggingConfig:
    directory: Path = Path("/var/log/neurobridge")
    filename: str = "neurobridge.log"
    level: str = "INFO"


@dataclass(frozen=True)
class GatewayConfig:
    server: ServerConfig
    ble: BleConfig
    recording: RecordingConfig
    algorithm: AlgorithmConfig
    download: DownloadConfig = field(default_factory=DownloadConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def load(path: str | Path) -> GatewayConfig:
    with Path(path).open("rb") as file:
        raw = tomllib.load(file)
    server, ble, recording, algorithm, download, logging = (raw.get(name, {}) for name in ("server", "ble", "recording", "algorithm", "download", "logging"))
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
    download_host = str(download.get("host", host))
    download_port = int(download.get("port", 8766))
    download_path = str(download.get("path", "/downloads")).rstrip("/") or "/downloads"
    try:
        download_address = ipaddress.ip_address(download_host)
    except ValueError as exc:
        raise ValueError("download.host must be a static IP address, not a DNS name or wildcard") from exc
    if not (download_address.is_private or download_address.is_loopback) or not 1 <= download_port <= 65535 or not download_path.startswith("/"):
        raise ValueError("download host must be private/loopback; port and HTTP path are invalid")
    log_directory = Path(logging.get("directory", "/var/log/neurobridge"))
    log_filename = str(logging.get("filename", "neurobridge.log"))
    log_level = str(logging.get("level", "INFO")).upper()
    if Path(log_filename).name != log_filename or not log_filename.endswith(".log"):
        raise ValueError("logging.filename must be a plain .log filename")
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("logging.level is invalid")
    return GatewayConfig(
        server=ServerConfig(host, port, endpoint),
        ble=BleConfig(bool(ble.get("enabled", False)), ble.get("device_name") or None, str(ble.get("model_nbr_uuid", "0000ff10-1212-abcd-1523-785feabcd123")).lower(), int(ble.get("scan_timeout_seconds", 5)), int(ble.get("reconnect_delay_seconds", 3))),
        recording=RecordingConfig(Path(recording.get("directory", "./recordings")), recording.get("subject_id") or None, recording.get("replay_recording_id") or None, replay_speed),
        algorithm=AlgorithmConfig(bool(algorithm.get("enabled", False)), tuple(algorithm.get("command", []))),
        download=DownloadConfig(bool(download.get("enabled", False)), download_host, download_port, download_path),
        logging=LoggingConfig(log_directory, log_filename, log_level),
    )
