from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
from pathlib import Path
import re
import tomllib

DEFAULT_ALGORITHM_COMMAND = ("/usr/local/lib/neurobridge/neurobridge_affective_bridge",)


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    path: str


@dataclass(frozen=True)
class BleConfig:
    enabled: bool
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
class NetworkConfig:
    """B-side endpoint allocation mode; DHCP remains an isolated opt-in service."""

    mode: str = "static"
    interface: str | None = None
    subnet_cidr: str | None = None
    dhcp_range_start: str | None = None
    dhcp_range_end: str | None = None
    dhcp_lease_time: str = "12h"


@dataclass(frozen=True)
class GatewayConfig:
    server: ServerConfig
    ble: BleConfig
    recording: RecordingConfig
    algorithm: AlgorithmConfig
    download: DownloadConfig = field(default_factory=DownloadConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)


def load(path: str | Path) -> GatewayConfig:
    with Path(path).open("rb") as file:
        raw = tomllib.load(file)
    server, ble, recording, algorithm, download, logging, network = (raw.get(name, {}) for name in ("server", "ble", "recording", "algorithm", "download", "logging", "network"))
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
    network_mode = str(network.get("mode", "static"))
    interface = network.get("interface") or None
    subnet_cidr = network.get("subnet_cidr") or None
    dhcp_range_start = network.get("dhcp_range_start") or None
    dhcp_range_end = network.get("dhcp_range_end") or None
    dhcp_lease_time = str(network.get("dhcp_lease_time", "12h"))
    if network_mode not in {"static", "dhcp"}:
        raise ValueError("network.mode must be static or dhcp")
    if interface is not None and interface != "auto" and (
        not isinstance(interface, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}", interface) is None
    ):
        raise ValueError("network.interface is invalid")
    if subnet_cidr is not None:
        try:
            subnet = ipaddress.ip_network(str(subnet_cidr), strict=True)
        except ValueError as exc:
            raise ValueError("network.subnet_cidr is invalid") from exc
        if subnet.version != 4 or address.version != 4 or address not in subnet or address in {subnet.network_address, subnet.broadcast_address}:
            raise ValueError("network.subnet_cidr must contain server.host as a usable IPv4 address")
    if network_mode == "dhcp":
        if not all((interface, subnet_cidr, dhcp_range_start, dhcp_range_end)):
            raise ValueError("network.interface, subnet_cidr, and DHCP range are required in dhcp mode")
        if interface == "auto":
            raise ValueError("network.interface must explicitly name the DHCP interface")
        try:
            subnet = ipaddress.ip_network(str(subnet_cidr), strict=True)
            range_start, range_end = ipaddress.ip_address(str(dhcp_range_start)), ipaddress.ip_address(str(dhcp_range_end))
        except ValueError as exc:
            raise ValueError("network DHCP subnet or range is invalid") from exc
        if subnet.version != 4 or address not in subnet or range_start not in subnet or range_end not in subnet or int(range_start) > int(range_end) or address in {range_start, range_end, subnet.network_address, subnet.broadcast_address} or range_end in {subnet.network_address, subnet.broadcast_address}:
            raise ValueError("network DHCP range must be IPv4, inside subnet, and exclude server.host")
        if not dhcp_lease_time[:-1].isdigit() or dhcp_lease_time[-1:] not in {"m", "h", "d"} or int(dhcp_lease_time[:-1]) <= 0:
            raise ValueError("network.dhcp_lease_time must use a positive m, h, or d duration")
    return GatewayConfig(
        server=ServerConfig(host, port, endpoint),
        ble=BleConfig(bool(ble.get("enabled", False)), str(ble.get("model_nbr_uuid", "0000ff10-1212-abcd-1523-785feabcd123")).lower(), int(ble.get("scan_timeout_seconds", 5)), int(ble.get("reconnect_delay_seconds", 3))),
        recording=RecordingConfig(Path(recording.get("directory", "./recordings")), recording.get("subject_id") or None, recording.get("replay_recording_id") or None, replay_speed),
        # Ubuntu installation places the locked native bridge at this fixed path.
        # ``enabled`` is therefore the only setting an operator needs to change
        # after the bridge's real-data POC has been approved.  An explicit command
        # remains available for controlled development or recovery overrides.
        algorithm=AlgorithmConfig(
            bool(algorithm.get("enabled", True)),
            tuple(algorithm.get("command") or DEFAULT_ALGORITHM_COMMAND),
        ),
        download=DownloadConfig(bool(download.get("enabled", False)), download_host, download_port, download_path),
        logging=LoggingConfig(log_directory, log_filename, log_level),
        network=NetworkConfig(network_mode, interface, subnet_cidr, dhcp_range_start, dhcp_range_end, dhcp_lease_time),
    )
