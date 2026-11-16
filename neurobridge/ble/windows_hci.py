"""Read Windows controller events in memory and log link diagnostics only."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import sys
from threading import Lock
from typing import Any


LOG = logging.getLogger(__name__)
_BTHPORT = "{8A1F9517-3A8C-4A9E-A018-4F17A200F277}"
_HCI_RAW_KEYWORD = 0x8000000000000000
_HCI_RAW_EVENT = 402
_FILETIME_EPOCH = 116444736000000000
_REASONS = {
    0x05: "authentication_failure",
    0x08: "connection_timeout",
    0x13: "remote_user_terminated",
    0x14: "remote_low_resources",
    0x15: "remote_power_off",
    0x16: "local_host_terminated",
    0x22: "link_layer_response_timeout",
    0x3B: "unacceptable_connection_parameters",
}


def _u16(packet: bytes, offset: int) -> int:
    return int.from_bytes(packet[offset:offset + 2], "little")


def _event_time(header: object) -> str:
    timestamp = header.get("TimeStamp") if isinstance(header, dict) else None
    if type(timestamp) is not int or timestamp < _FILETIME_EPOCH:
        return "unknown"
    try:
        return datetime.fromtimestamp((timestamp - _FILETIME_EPOCH) / 10_000_000, timezone.utc).isoformat(timespec="milliseconds")
    except (ValueError, OverflowError, OSError):
        return "unknown"


class WindowsHciDiagnostics:
    """Track small HCI connection handles without logging addresses or payloads."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._address: bytes | None = None
        self._attempt: int | None = None
        self._links: dict[int, int | None] = {}
        self._capture = None

    def track(self, address: str, attempt: int | None = None) -> None:
        try:
            peer = bytes.fromhex(address.replace(":", "").replace("-", ""))
        except ValueError:
            peer = b""
        with self._lock:
            self._address = peer[::-1] if len(peer) == 6 else None
            self._attempt = attempt

    def start(self) -> None:
        if sys.platform != "win32" or self._capture is not None:
            return
        capture = None
        try:
            from etw import ETW, GUID, ProviderInfo

            provider = ProviderInfo("BTHPORT", GUID(_BTHPORT), any_keywords=_HCI_RAW_KEYWORD)
            capture = ETW(
                session_name=f"NeuroBridge-HCI-{os.getpid()}",
                providers=[provider],
                event_id_filters=[_HCI_RAW_EVENT],
                event_callback=self.observe,
                ignore_exists_error=False,
                ring_buf_size=64,
                max_buffers=32,
            )
            capture.start()
        except Exception as exc:
            if capture is not None and capture.provider is not None:
                try:
                    capture.provider.stop()
                except Exception:
                    LOG.warning("Windows Bluetooth HCI trace cleanup failed after startup error")
            LOG.warning("Windows Bluetooth HCI trace unavailable: errorType=%s winError=%s",
                        type(exc).__name__, getattr(exc, "winerror", None))
            return
        self._capture = capture
        LOG.info("Windows Bluetooth HCI trace active: mode=realtime eventId=%s", _HCI_RAW_EVENT)

    def stop(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            try:
                capture.stop()
            except Exception as exc:
                LOG.warning("Windows Bluetooth HCI trace stop failed: errorType=%s winError=%s",
                            type(exc).__name__, getattr(exc, "winerror", None))

    def observe(self, event: tuple[int, dict[str, Any]]) -> None:
        event_id, data = event
        kind = data.get("BIP_Type")
        if event_id != _HCI_RAW_EVENT or kind not in ("1", "2", 1, 2):
            return
        encoded = data.get("BIP_Data")
        if not isinstance(encoded, str) or not encoded.startswith("0x"):
            return
        try:
            packet = bytes.fromhex(encoded[2:])
        except ValueError:
            return
        if kind in ("1", 1):
            if len(packet) == 6 and packet[:3] == b"\x06\x04\x03":
                self._log_disconnect(data, "disconnect_command", _u16(packet, 3), packet[5])
        elif len(packet) >= 2 and len(packet) == 2 + packet[1]:
            if len(packet) == 6 and packet[0] == 0x05:
                self._log_disconnect(data, "disconnection_complete", _u16(packet, 3), packet[5], packet[2])
            if packet[0] == 0x3E:
                self._observe_le_event(data, packet)

    def _observe_le_event(self, data: dict[str, Any], packet: bytes) -> None:
        if (len(packet), packet[2] if len(packet) > 2 else None) in ((21, 0x01), (33, 0x0A)):
            if packet[3] != 0:
                return
            handle = _u16(packet, 4)
            with self._lock:
                peers = (packet[8:14], packet[20:26]) if packet[2] == 0x0A else (packet[8:14],)
                if self._address in peers:
                    self._links[handle] = self._attempt
                else:
                    self._links.pop(handle, None)
                    return
                attempt = self._attempt
            index = 26 if packet[2] == 0x0A else 14
            self._log_parameters(data, "le_connection_complete", handle, attempt, packet, index)
        elif len(packet) == 12 and packet[2] == 0x03:
            handle = _u16(packet, 4)
            with self._lock:
                if handle not in self._links:
                    return
                attempt = self._links[handle]
            self._log_parameters(data, "le_connection_update", handle, attempt, packet, 6)

    def _log_parameters(self, data: dict[str, Any], kind: str, handle: int, attempt: int | None,
                        packet: bytes, index: int) -> None:
        LOG.info("Windows Bluetooth HCI: event=%s eventUtc=%s association=headband attempt=%s handle=0x%04X "
                 "status=0x%02X intervalMs=%g peripheralLatency=%d supervisionTimeoutMs=%d",
                 kind, _event_time(data.get("EventHeader")), attempt, handle, packet[3],
                 _u16(packet, index) * 1.25, _u16(packet, index + 2), _u16(packet, index + 4) * 10)

    def _log_disconnect(self, data: dict[str, Any], kind: str, handle: int, reason: int,
                        status: int | None = None) -> None:
        with self._lock:
            associated = handle in self._links
            attempt = self._links.get(handle)
            if kind == "disconnection_complete" and status == 0:
                self._links.pop(handle, None)
        LOG.info("Windows Bluetooth HCI: event=%s eventUtc=%s association=%s attempt=%s handle=0x%04X "
                 "status=%s reason=0x%02X reasonName=%s",
                 kind, _event_time(data.get("EventHeader")), "headband" if associated else "unmatched",
                 attempt, handle, f"0x{status:02X}" if status is not None else None,
                 reason, _REASONS.get(reason, "unknown"))
