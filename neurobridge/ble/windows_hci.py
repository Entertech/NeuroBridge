"""Read Windows controller events in memory and log link diagnostics only."""

from __future__ import annotations

from collections import deque
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
_ACL_DIRECTIONS = {"3": "controller_to_host", "4": "host_to_controller"}
_PARAMETER_COMMANDS = {
    0x2013: "le_connection_update_command",
    0x2020: "le_remote_parameter_reply",
    0x2021: "le_remote_parameter_negative_reply",
}
_REASONS = {
    0x05: "authentication_failure",
    0x08: "connection_timeout",
    0x0C: "command_disallowed",
    0x12: "invalid_hci_command_parameters",
    0x13: "remote_user_terminated",
    0x14: "remote_low_resources",
    0x15: "remote_power_off",
    0x16: "local_host_terminated",
    0x22: "link_layer_response_timeout",
    0x30: "parameter_out_of_mandatory_range",
    0x3B: "unacceptable_connection_parameters",
}


def _u16(packet: bytes, offset: int) -> int:
    return int.from_bytes(packet[offset:offset + 2], "little")


def _parameter_details(packet: bytes, index: int) -> str:
    minimum, maximum, latency, timeout = (
        _u16(packet, index + offset) for offset in (0, 2, 4, 6)
    )
    issues = []
    if not 6 <= minimum <= 3200:
        issues.append("interval_min_out_of_range")
    if not 6 <= maximum <= 3200:
        issues.append("interval_max_out_of_range")
    if minimum > maximum:
        issues.append("interval_min_above_max")
    if latency > 499:
        issues.append("latency_out_of_range")
    if not 10 <= timeout <= 3200:
        issues.append("supervision_timeout_out_of_range")
    # This is the lower bound even when the subrate factor is unknown.
    if timeout * 4 <= (latency + 1) * maximum:
        issues.append("supervision_timeout_below_required_margin")
    return (
        f"intervalMinMs={minimum * 1.25:g} intervalMaxMs={maximum * 1.25:g} "
        f"peripheralLatency={latency} supervisionTimeoutMs={timeout * 10} "
        f"parameterIssues={','.join(issues) or 'none'}"
    )


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

    def __init__(self, *, log_unmatched_parameters: bool = False) -> None:
        self._lock = Lock()
        self._address: bytes | None = None
        self._attempt: int | None = None
        self._links: dict[int, int | None] = {}
        self._pending_updates: deque[tuple[int, bool, int | None]] = deque(maxlen=32)
        self._log_unmatched_parameters = log_unmatched_parameters
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
        kind = str(data.get("BIP_Type"))
        if event_id != _HCI_RAW_EVENT or kind not in ("1", "2", *_ACL_DIRECTIONS):
            return
        encoded = data.get("BIP_Data")
        if not isinstance(encoded, str) or not encoded.startswith("0x"):
            return
        try:
            packet = bytes.fromhex(encoded[2:])
        except ValueError:
            return
        if kind == "1":
            self._observe_command(data, packet)
        elif kind in _ACL_DIRECTIONS:
            self._observe_le_signaling(data, packet, _ACL_DIRECTIONS[kind])
        elif len(packet) >= 2 and len(packet) == 2 + packet[1]:
            if len(packet) == 6 and packet[0] == 0x05:
                self._log_disconnect(data, "disconnection_complete", _u16(packet, 3), packet[5], packet[2])
            elif packet[0] == 0x3E:
                self._observe_le_event(data, packet)
            elif packet[0] in (0x0E, 0x0F):
                self._observe_command_result(data, packet)

    def _observe_command(self, data: dict[str, Any], packet: bytes) -> None:
        if len(packet) < 5 or len(packet) != 3 + packet[2]:
            return
        opcode, handle = _u16(packet, 0), _u16(packet, 3)
        if opcode == 0x0406 and len(packet) == 6:
            self._log_disconnect(data, "disconnect_command", handle, packet[5])
        elif opcode in (0x2013, 0x2020) and len(packet) == 17:
            if opcode == 0x2013:
                with self._lock:
                    self._pending_updates.append((handle, handle in self._links, self._links.get(handle)))
            details = (f"{_parameter_details(packet, 5)} "
                       f"minCeMs={_u16(packet, 13) * 0.625:g} maxCeMs={_u16(packet, 15) * 0.625:g}")
            self._log_negotiation(data, _PARAMETER_COMMANDS[opcode], handle, details)
        elif opcode == 0x2021 and len(packet) == 6:
            reason = packet[5]
            self._log_negotiation(data, _PARAMETER_COMMANDS[opcode], handle,
                                  f"reason=0x{reason:02X} reasonName={_REASONS.get(reason, 'unknown')}")

    def _observe_command_result(self, data: dict[str, Any], packet: bytes) -> None:
        if len(packet) == 6 and packet[0] == 0x0F and _u16(packet, 4) == 0x2013:
            with self._lock:
                if not self._pending_updates:
                    return
                handle, associated, attempt = self._pending_updates.popleft()
            self._log_negotiation(data, "le_connection_update_command_status", handle,
                                  f"status=0x{packet[2]:02X}", link=(associated, attempt))
        elif len(packet) == 8 and packet[0] == 0x0E:
            opcode = _u16(packet, 3)
            if opcode in (0x2020, 0x2021):
                self._log_negotiation(data, _PARAMETER_COMMANDS[opcode] + "_complete",
                                      _u16(packet, 6), f"status=0x{packet[5]:02X}")

    def _observe_le_signaling(self, data: dict[str, Any], packet: bytes, direction: str) -> None:
        # Only parse complete LE signaling PDUs. ATT and sensor traffic are skipped.
        if len(packet) not in (14, 20) or _u16(packet, 2) != len(packet) - 4:
            return
        flags = _u16(packet, 0)
        if flags & 0xD000 or _u16(packet, 6) != 0x0005 or _u16(packet, 4) != len(packet) - 8:
            return
        handle = flags & 0x0FFF
        code, identifier = packet[8:10]
        if identifier == 0 or _u16(packet, 10) != len(packet) - 12:
            return
        if code == 0x12 and len(packet) == 20:
            name, details = "l2cap_parameter_update_request", _parameter_details(packet, 12)
        elif code == 0x13 and len(packet) == 14:
            result = _u16(packet, 12)
            outcome = {0: "accepted", 1: "rejected"}.get(result, "unknown")
            name, details = "l2cap_parameter_update_response", f"result=0x{result:04X} decision={outcome}"
        else:
            return
        self._log_negotiation(data, name, handle, f"direction={direction} identifier={identifier} {details}")

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
            status = packet[3]
            details = f"status=0x{status:02X}"
            if status == 0:
                details += (f" intervalMs={_u16(packet, 6) * 1.25:g} peripheralLatency={_u16(packet, 8)}"
                            f" supervisionTimeoutMs={_u16(packet, 10) * 10}")
            self._log_negotiation(data, "le_connection_update", handle, details)
        elif len(packet) == 13 and packet[2] == 0x06:
            self._log_negotiation(data, "le_remote_parameter_request", _u16(packet, 3), _parameter_details(packet, 5))

    def _log_negotiation(self, data: dict[str, Any], kind: str, handle: int, details: str,
                         *, link: tuple[bool, int | None] | None = None) -> None:
        if link is None:
            with self._lock:
                link = (handle in self._links, self._links.get(handle))
        associated, attempt = link
        if associated or self._log_unmatched_parameters:
            LOG.info("Windows Bluetooth HCI: event=%s eventUtc=%s association=%s attempt=%s handle=0x%04X %s",
                     kind, _event_time(data.get("EventHeader")), "headband" if associated else "unmatched",
                     attempt, handle, details)

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
