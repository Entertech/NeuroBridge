from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import math
from pathlib import Path
import re
import tomllib

from .configuration.migration import migrate

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
    window_interval_ms: int = 600
    stale_after_ms: int = 1800


@dataclass(frozen=True)
class PipelineConfig:
    source_queue_size: int = 64
    source_enqueue_timeout_ms: int = 50
    algorithm_queue_size: int = 8
    persistence_timeout_ms: int = 100
    shutdown_timeout_ms: int = 5000
    send_timeout_ms: int = 5000
    metrics_interval_seconds: int = 10


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
    transport_trace_enabled: bool = False
    transport_trace_max_bytes: int = 1024 * 1024


@dataclass(frozen=True)
class AlgorithmConfig:
    enabled: bool
    command: tuple[str, ...]
    request_timeout_ms: int = 2000


@dataclass(frozen=True)
class StorageConfig:
    warning_threshold_bytes: int = 5 * 1024**3
    critical_threshold_bytes: int = 1 * 1024**3
    segment_duration_seconds: int = 600
    segment_max_bytes: int = 256 * 1024**2
    writer_queue_size: int = 1024
    recovery_margin_bytes: int = 1 * 1024**3
    recovery_checks: int = 3
    fsync_interval_records: int = 64
    session_retention_days: int = 30
    auto_cleanup_enabled: bool = False


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
    rotation_mode: str = "size"
    max_bytes: int = 10 * 1024**2
    backup_count: int = 14


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
    storage: StorageConfig = field(default_factory=StorageConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    config_schema_version: int = 1
    profile: str | None = None


def load(
    path: str | Path,
    *,
    defaults_path: str | Path | None = None,
    system_path: str | Path | None = None,
) -> GatewayConfig:
    """Load defaults -> system configuration -> explicit override."""

    raw: dict[str, object] = {}
    for candidate in (defaults_path, system_path, path):
        if candidate is None:
            continue
        with Path(candidate).open("rb") as file:
            raw = _merge_config(raw, tomllib.load(file))
    raw = migrate(raw)
    section_fields = {
        "server": {"host", "port", "path"},
        "ble": {"enabled", "device_name", "model_nbr_uuid", "scan_timeout_seconds", "reconnect_delay_seconds"},
        "recording": {"directory", "subject_id", "replay_recording_id", "replay_speed", "transport_trace_enabled", "transport_trace_max_bytes"},
        "algorithm": {"enabled", "command", "request_timeout_ms"},
        "download": {"enabled", "host", "port", "path"},
        "logging": {"directory", "filename", "level", "rotation_mode", "max_bytes", "backup_count"},
        "network": {"mode", "interface", "subnet_cidr", "dhcp_range_start", "dhcp_range_end", "dhcp_lease_time"},
        "access": {"mode"},
        "local_ui": {"enabled", "host", "port", "directory"},
        "data_source": {"type", "window_interval_ms", "stale_after_ms"},
        "serial": {"device", "candidate_types", "baud_rate", "handshake_timeout_ms", "command_response_timeout_ms", "data_timeout_seconds", "reconnect_delay_seconds", "stats_interval_seconds", "max_buffer_bytes", "dtr", "rts"},
        "storage": {"warning_threshold_bytes", "critical_threshold_bytes", "segment_duration_seconds", "segment_max_bytes", "writer_queue_size", "recovery_margin_bytes", "recovery_checks", "fsync_interval_records", "session_retention_days", "auto_cleanup_enabled"},
        "pipeline": set(PipelineConfig.__dataclass_fields__),
    }
    unknown_top_level = set(raw) - {"config_schema_version", "profile", *section_fields}
    if unknown_top_level:
        raise ValueError(f"Unknown top-level configuration fields: {', '.join(sorted(unknown_top_level))}")
    for section, allowed_fields in section_fields.items():
        values = raw.get(section, {})
        if not isinstance(values, dict):
            raise ValueError(f"{section} must be a TOML table")
        unknown = set(values) - allowed_fields
        if unknown:
            raise ValueError(f"Unknown {section} configuration fields: {', '.join(sorted(unknown))}")
    _validate_types(raw)
    server, ble, recording, algorithm, download, logging, network, access, local_ui, data_source, serial, storage = (
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
            "storage",
        )
    )
    config_schema_version = raw.get("config_schema_version", 1)
    if not isinstance(config_schema_version, int) or isinstance(config_schema_version, bool) or config_schema_version != 1:
        raise ValueError("config_schema_version must be 1")
    if "profile" in raw and not isinstance(raw["profile"], str):
        raise ValueError("profile must be a string")
    profile = raw.get("profile") or None
    if profile is not None and (
        not isinstance(profile, str)
        or profile not in {
            "macos_headband_wired",
            "ubuntu_headband_wired",
            "kylin_headset_local",
            "windows_headset_local",
        }
    ):
        raise ValueError("profile is not a supported deployment profile")
    replay_speed = float(recording.get("replay_speed", 1))
    trace_limit = recording.get("transport_trace_max_bytes", 1024 * 1024)
    if trace_limit <= 0:
        raise ValueError("recording.transport_trace_max_bytes must be positive")
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
    rotation_mode = logging.get("rotation_mode", "size")
    max_bytes = logging.get("max_bytes", 10 * 1024**2)
    backup_count = logging.get("backup_count", 14)
    if rotation_mode not in {"size", "external"} or max_bytes <= 0 or backup_count <= 0:
        raise ValueError("logging rotation_mode must be size/external and limits must be positive")
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
    if "type" not in data_source:
        raise ValueError("data_source.type must be explicitly configured as bluetooth, serial, or usb")
    data_source_type = str(data_source["type"])
    if data_source_type not in {"bluetooth", "serial", "usb"}:
        raise ValueError("data_source.type must be bluetooth, serial, or usb")
    window_interval_ms = data_source.get("window_interval_ms", 600)
    stale_after_ms = data_source.get("stale_after_ms", 1800)
    if not isinstance(window_interval_ms, int) or isinstance(window_interval_ms, bool) or window_interval_ms <= 0:
        raise ValueError("data_source.window_interval_ms must be a positive integer")
    if window_interval_ms != 600:
        raise ValueError("data_source.window_interval_ms must be 600 for the current northbound contract")
    if not isinstance(stale_after_ms, int) or isinstance(stale_after_ms, bool) or stale_after_ms <= window_interval_ms:
        raise ValueError("data_source.stale_after_ms must be an integer greater than window_interval_ms")
    replay_recording_id = recording.get("replay_recording_id") or None
    if data_source_type == "serial" and replay_recording_id:
        raise ValueError("recording.replay_recording_id is not supported when data_source.type is serial")

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
        windows_com = re.fullmatch(r"(?:\\\\\.\\)?COM[1-9][0-9]*", serial_device, re.IGNORECASE)
        if serial_device != "auto" and not Path(serial_device).is_absolute() and not (profile == "windows_headset_local" and windows_com):
            raise ValueError("serial.device must be auto, an absolute device path, or a COM port for the Windows profile")
        candidate_types_value = serial.get("candidate_types", ["ttyACM", "ttyUSB"])
        if not isinstance(candidate_types_value, list) or not candidate_types_value:
            raise ValueError("serial.candidate_types must be a non-empty array")
        candidate_types = tuple(str(value) for value in candidate_types_value)
        allowed_candidate_types = {"COM"} if profile == "windows_headset_local" else {"ttyACM", "ttyUSB"}
        if any(value not in allowed_candidate_types for value in candidate_types):
            raise ValueError(
                "serial.candidate_types only supports COM for the Windows profile "
                "or ttyACM/ttyUSB for POSIX profiles"
            )
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
    algorithm_timeout_ms = algorithm.get("request_timeout_ms", 2000)
    if not isinstance(algorithm_timeout_ms, int) or isinstance(algorithm_timeout_ms, bool) or not 100 <= algorithm_timeout_ms <= 120000:
        raise ValueError("algorithm.request_timeout_ms must be an integer between 100 and 120000")
    storage_values = {
        "warning_threshold_bytes": storage.get("warning_threshold_bytes", 5 * 1024**3),
        "critical_threshold_bytes": storage.get("critical_threshold_bytes", 1 * 1024**3),
        "segment_duration_seconds": storage.get("segment_duration_seconds", 600),
        "segment_max_bytes": storage.get("segment_max_bytes", 256 * 1024**2),
        "writer_queue_size": storage.get("writer_queue_size", 1024),
        "recovery_margin_bytes": storage.get("recovery_margin_bytes", 1 * 1024**3),
        "recovery_checks": storage.get("recovery_checks", 3),
        "fsync_interval_records": storage.get("fsync_interval_records", 64),
        "session_retention_days": storage.get("session_retention_days", 30),
    }
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in storage_values.values()):
        raise ValueError("storage size, duration, and queue settings must be positive integers")
    if storage_values["critical_threshold_bytes"] >= storage_values["warning_threshold_bytes"]:
        raise ValueError("storage.critical_threshold_bytes must be below storage.warning_threshold_bytes")
    auto_cleanup_enabled = storage.get("auto_cleanup_enabled", False)
    if not isinstance(auto_cleanup_enabled, bool):
        raise ValueError("storage.auto_cleanup_enabled must be boolean")
    pipeline_values = raw.get("pipeline", {})
    if any(type(value) is not int or value <= 0 for value in pipeline_values.values()):
        raise ValueError("pipeline limits and intervals must be positive integers")
    config = GatewayConfig(
        server=ServerConfig(host, port, endpoint),
        ble=ble_config,
        recording=RecordingConfig(recording_directory, recording.get("subject_id") or None, replay_recording_id, replay_speed, recording.get("transport_trace_enabled", False), trace_limit),
        # Ubuntu installation places the locked native bridge at this fixed path.
        # ``enabled`` is therefore the only setting an operator needs to change
        # after the bridge's real-data POC has been approved.  An explicit command
        # remains available for controlled development or recovery overrides.
        algorithm=AlgorithmConfig(
            bool(algorithm.get("enabled", True)),
            tuple(algorithm.get("command") or DEFAULT_ALGORITHM_COMMAND),
            algorithm_timeout_ms,
        ),
        download=DownloadConfig(bool(download.get("enabled", False)), download_host, download_port, download_path),
        logging=LoggingConfig(log_directory, log_filename, log_level, rotation_mode, max_bytes, backup_count),
        network=NetworkConfig(network_mode, interface, subnet_cidr, dhcp_range_start, dhcp_range_end, dhcp_lease_time),
        access=AccessConfig(access_mode),
        local_ui=LocalUiConfig(
            local_ui_enabled,
            local_ui_host,
            local_ui_port,
            local_ui_directory,
        ),
        data_source=DataSourceConfig(data_source_type, window_interval_ms, stale_after_ms),
        serial=serial_config,
        storage=StorageConfig(**storage_values, auto_cleanup_enabled=auto_cleanup_enabled),
        pipeline=PipelineConfig(**pipeline_values),
        config_schema_version=config_schema_version,
        profile=profile,
    )
    from .northbound.strategy import access_strategy

    access_strategy(config.access.mode).validate(config)
    return config


def _merge_config(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _merge_config(current, value)
        else:
            result[key] = value
    return result


def _validate_types(raw: dict) -> None:
    """Reject coercion (especially bool("false")) before using configuration.

    Inactive device sections keep their legacy ignore semantics; no dormant
    transport parameters are consumed or used to load drivers.
    """
    boolean = {"ble.enabled", "algorithm.enabled", "download.enabled", "local_ui.enabled",
               "serial.dtr", "serial.rts", "storage.auto_cleanup_enabled", "recording.transport_trace_enabled"}
    integer = {"server.port", "download.port", "local_ui.port", "ble.scan_timeout_seconds",
               "ble.reconnect_delay_seconds", "serial.baud_rate", "serial.handshake_timeout_ms",
               "serial.command_response_timeout_ms", "serial.max_buffer_bytes",
               "algorithm.request_timeout_ms", "data_source.window_interval_ms", "data_source.stale_after_ms",
               "logging.max_bytes", "logging.backup_count", "recording.transport_trace_max_bytes"}
    number = {"serial.data_timeout_seconds", "serial.reconnect_delay_seconds", "serial.stats_interval_seconds",
              "recording.replay_speed"}
    arrays = {"algorithm.command", "serial.candidate_types"}
    for section, fields in raw.items():
        if not isinstance(fields, dict):
            continue
        selected = raw.get("data_source", {}).get("type")
        if (section == "ble" and selected != "bluetooth") or (section == "serial" and selected != "serial"):
            continue
        for field, value in fields.items():
            key = f"{section}.{field}"
            if section == "pipeline":  # detailed limit error below
                continue
            if key in boolean:
                valid, expected = type(value) is bool, "boolean"
            elif key in integer or section == "storage":
                valid, expected = type(value) is int, "integer"
            elif key in number:
                valid = type(value) in {int, float} and math.isfinite(value)
                expected = "finite number"
            elif key in arrays:
                valid = isinstance(value, list) and all(isinstance(item, str) and item for item in value)
                expected = "array of non-empty strings"
            else:
                valid, expected = isinstance(value, str), "string"
            if not valid:
                raise ValueError(f"{key} must be {expected}")
