"""Windows COM implementation of the shared headset RawDataSource contract."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

from ...config import SerialConfig
from .serial_posix import PosixSerialSource


_COM_PATTERN = re.compile(r"(?:\\\\\.\\)?COM([1-9][0-9]*)", re.IGNORECASE)


def discover_windows_com_candidates(
    config: SerialConfig,
    *,
    port_provider=None,
) -> list[str]:
    """Return deterministic USB-derived COM names without opening them."""

    if config.device != "auto" and _COM_PATTERN.fullmatch(config.device) is None:
        return []
    if port_provider is None:
        from serial.tools import list_ports

        port_provider = list_ports.comports
    candidates: list[tuple[int, str]] = []
    for port in port_provider():
        device = str(getattr(port, "device", ""))
        match = _COM_PATTERN.fullmatch(device)
        if match is None:
            continue
        if config.device != "auto" and int(_COM_PATTERN.fullmatch(config.device).group(1)) != int(match.group(1)):
            continue
        hwid = str(getattr(port, "hwid", ""))
        if getattr(port, "vid", None) is None and getattr(port, "pid", None) is None and "USB" not in hwid.upper():
            continue
        candidates.append((int(match.group(1)), device))
    return [device for _number, device in sorted(candidates)]


def windows_com_metadata(path: str, *, port_provider=None) -> dict[str, str | None]:
    if port_provider is None:
        from serial.tools import list_ports

        port_provider = list_ports.comports
    selected = next((port for port in port_provider() if str(getattr(port, "device", "")).lower() == path.lower()), None)
    vid = getattr(selected, "vid", None)
    pid = getattr(selected, "pid", None)
    return {
        "resolvedPath": path,
        "vid": f"{vid:04x}" if isinstance(vid, int) else None,
        "pid": f"{pid:04x}" if isinstance(pid, int) else None,
        "usbSerial": str(getattr(selected, "serial_number", "")) or None,
        "interface": str(getattr(selected, "interface", "")) or None,
        "driver": "windows-com",
        "usbParent": str(getattr(selected, "location", "")) or None,
        "physicalPath": path,
    }


def open_windows_com(path: str, config: SerialConfig) -> Any:
    import serial

    client = serial.Serial(
        port=None,
        baudrate=config.baud_rate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.1,
        write_timeout=config.command_response_timeout_ms / 1000,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )
    # Apply modem-control levels while the COM port is still closed so opening
    # it cannot pulse DTR/RTS and reset the headset unexpectedly.
    client.dtr = config.dtr
    client.rts = config.rts
    client.port = path
    client.open()
    return client


class WindowsSerialSource(PosixSerialSource):
    """Use Windows discovery/opening while reusing the session and parser ports."""

    def __init__(
        self,
        config: SerialConfig,
        device_ready,
        error=None,
        *,
        queue_size: int = 64,
        external_control: bool = True,
        application_control: bool = False,
        enqueue_timeout_ms: int = 50,
        port_provider=None,
        serial_factory=open_windows_com,
        resume_state_path=None,
        probe_algorithm_context=None,
    ) -> None:
        def candidates(value: SerialConfig) -> Iterable[str]:
            return discover_windows_com_candidates(value, port_provider=port_provider)

        def identity(path: str) -> dict[str, str | None]:
            return windows_com_metadata(path, port_provider=port_provider)

        from .serial_resume import WindowsHeadsetResume
        restart_probe = (WindowsHeadsetResume(resume_state_path, identity, probe_algorithm_context)
                         if resume_state_path is not None and probe_algorithm_context is not None else None)
        super().__init__(
            config,
            device_ready,
            error,
            queue_size=queue_size,
            external_control=external_control,
            application_control=application_control,
            enqueue_timeout_ms=enqueue_timeout_ms,
            candidate_provider=candidates,
            serial_factory=serial_factory,
            identity_provider=identity,
            restart_probe=restart_probe,
        )
