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
    device_name: str | None
    model_nbr_uuid: str
    scan_timeout_seconds: int
    reconnect_delay_seconds: int


@dataclass(frozen=True)
class DataSourceConfig:
    """Select exactly one device-side transport strategy."""

    type: str = "bluetooth"


@dataclass(frozen=True)
class SerialConfig:
    device: str = "auto"
    candidate_types: tuple[str, ...] = ("ttyACM", "ttyUSB")
    baud_rate: int = 115200
    handshake_timeout_ms: int = 1000
    command_response_timeout_ms: int = 1000
    data_timeout_seconds: float = 5.0
    reconnect_delay_seconds: float = 3.0
    stats_interval_seconds: float = 10.0
    max_buffer_bytes: int = 65536
    dtr: bool = False
    rts: bool = False


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
    """HTTP downloads exposed by the selected access strategy."""

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
    """Legacy wired endpoint allocation; local browser leaves fields empty."""

    mode: str = "static"
    interface: str | None = None
    subnet_cidr: str | None = None
    dhcp_range_start: str | None = None
    dhcp_range_end: str | None = None
    dhcp_lease_time: str = "12h"


@dataclass(frozen=True)
class AccessConfig:
    """Select how a browser/client reaches the unchanged WebSocket contract."""

    mode: str = "local_browser"


@dataclass(frozen=True)
class LocalUiConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    directory: Path | None = None


@dataclass(frozen=True)
class GatewayConfig:
    server: ServerConfig
    ble: BleConfig
    recording: RecordingConfig
    algorithm: AlgorithmConfig
    download: DownloadConfig = field(default_factory=DownloadConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    access: AccessConfig = field(default_factory=AccessConfig)
    local_ui: LocalUiConfig = field(default_factory=LocalUiConfig)
    data_source: DataSourceConfig = field(default_factory=DataSourceConfig)
    serial: SerialConfig = field(default_factory=SerialConfig)


def load(path: str | Path) -> GatewayConfig:
    with Path(path).open("rb") as file:
        raw = tomllib.load(file)
    server, ble, recording, algorithm, download, logging, network, access, local_ui, data_source, serial = (
        raw.get(name, {})
        for name in (
            "server",
            "ble",
            "recording",
            "algorithm",
            "download",
            "logging",
            "network",
            "access",
            "local_ui",
            "data_source",
            "serial",
        )
    )
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
    # Existing deployments predate access.mode. Preserve their private-address
    # wired topology, while new/loopback configurations default to local UI.
    access_mode = str(access.get("mode") or ("local_browser" if address.is_loopback else "wired_b_side"))
    local_ui_host = str(local_ui.get("host", "127.0.0.1"))
    local_ui_port = int(local_ui.get("port", 8080))
    local_ui_enabled = bool(local_ui.get("enabled", access_mode == "local_browser"))
    try:
        local_ui_address = ipaddress.ip_address(local_ui_host)
    except ValueError as exc:
        raise ValueError("local_ui.host must be an IP address") from exc
    if not (local_ui_address.is_private or local_ui_address.is_loopback) or not 1 <= local_ui_port <= 65535:
        raise ValueError("local_ui host or port is invalid")
    local_ui_directory_value = local_ui.get("directory")
    local_ui_directory = Path(local_ui_directory_value) if local_ui_directory_value else None
    if local_ui_directory is not None and not local_ui_directory.is_absolute():
        raise ValueError("local_ui.directory must be absolute when configured")
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
    data_source_type = str(data_source.get("type", "bluetooth"))
    if data_source_type not in {"bluetooth", "serial", "usb"}:
        raise ValueError("data_source.type must be bluetooth, serial, or usb")

    # Parse and validate only the selected strategy. This lets an operator keep
    # dormant strategy sections for a controlled restart-based switch without
    # loading their drivers or being blocked by parameters that are not active.
    recording_directory = Path(recording.get("directory", "./recordings"))
    ble_config = BleConfig(False, None, "0000ff10-1212-abcd-1523-785feabcd123", 5, 3)
    if data_source_type == "bluetooth":
        ble_config = BleConfig(
            bool(ble.get("enabled", False)),
            ble.get("device_name") or None,
            str(ble.get("model_nbr_uuid", "0000ff10-1212-abcd-1523-785feabcd123")).lower(),
            int(ble.get("scan_timeout_seconds", 5)),
            int(ble.get("reconnect_delay_seconds", 3)),
        )
    serial_config = SerialConfig()
    if data_source_type == "serial":
        serial_device = str(serial.get("device", "auto"))
        if serial_device != "auto" and not Path(serial_device).is_absolute():
            raise ValueError("serial.device must be auto or an absolute device path")
        candidate_types_value = serial.get("candidate_types", ["ttyACM", "ttyUSB"])
        if not isinstance(candidate_types_value, list) or not candidate_types_value:
            raise ValueError("serial.candidate_types must be a non-empty array")
        candidate_types = tuple(str(value) for value in candidate_types_value)
        if any(value not in {"ttyACM", "ttyUSB"} for value in candidate_types):
            raise ValueError("serial.candidate_types only supports ttyACM and ttyUSB")
        baud_rate = int(serial.get("baud_rate", 115200))
        handshake_timeout_ms = int(serial.get("handshake_timeout_ms", 1000))
        command_response_timeout_ms = int(serial.get("command_response_timeout_ms", 1000))
        data_timeout_seconds = float(serial.get("data_timeout_seconds", 5))
        serial_reconnect_delay_seconds = float(serial.get("reconnect_delay_seconds", 3))
        stats_interval_seconds = float(serial.get("stats_interval_seconds", 10))
        max_buffer_bytes = int(serial.get("max_buffer_bytes", 65536))
        dtr = serial.get("dtr", False)
        rts = serial.get("rts", False)
        if not isinstance(dtr, bool) or not isinstance(rts, bool):
            raise ValueError("serial.dtr and serial.rts must be boolean")
        if baud_rate != 115200:
            raise ValueError("serial.baud_rate must be 115200 for the confirmed device protocol")
        if not 200 <= handshake_timeout_ms <= 30000:
            raise ValueError("serial.handshake_timeout_ms must be between 200 and 30000")
        if not 100 <= command_response_timeout_ms <= 30000:
            raise ValueError("serial.command_response_timeout_ms must be between 100 and 30000")
        if not 0.5 <= data_timeout_seconds <= 300:
            raise ValueError("serial.data_timeout_seconds must be between 0.5 and 300")
        if not 0.1 <= serial_reconnect_delay_seconds <= 300:
            raise ValueError("serial.reconnect_delay_seconds must be between 0.1 and 300")
        if not 1 <= stats_interval_seconds <= 3600:
            raise ValueError("serial.stats_interval_seconds must be between 1 and 3600")
        if not 1024 <= max_buffer_bytes <= 4 * 1024 * 1024:
            raise ValueError("serial.max_buffer_bytes must be between 1024 and 4194304")
        serial_config = SerialConfig(
            device=serial_device,
            candidate_types=candidate_types,
            baud_rate=baud_rate,
            handshake_timeout_ms=handshake_timeout_ms,
            command_response_timeout_ms=command_response_timeout_ms,
            data_timeout_seconds=data_timeout_seconds,
            reconnect_delay_seconds=serial_reconnect_delay_seconds,
            stats_interval_seconds=stats_interval_seconds,
            max_buffer_bytes=max_buffer_bytes,
            dtr=dtr,
            rts=rts,
        )
    config = GatewayConfig(
        server=ServerConfig(host, port, endpoint),
        ble=ble_config,
        recording=RecordingConfig(recording_directory, recording.get("subject_id") or None, recording.get("replay_recording_id") or None, replay_speed),
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
        access=AccessConfig(access_mode),
        local_ui=LocalUiConfig(
            local_ui_enabled,
            local_ui_host,
            local_ui_port,
            local_ui_directory,
        ),
        data_source=DataSourceConfig(data_source_type),
        serial=serial_config,
    )
    from .northbound.strategy import access_strategy

    access_strategy(config.access.mode).validate(config)
    return config
