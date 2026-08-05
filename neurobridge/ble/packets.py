from __future__ import annotations

from dataclasses import dataclass, field
import base64
import time

EEG_PACKET_BYTES = 14
NATIVE_HR_PACKET_BYTES = 16
HR_RAW_PACKET_BYTES = 20


@dataclass(frozen=True)
class RawPacket:
    characteristic: str
    received_ms: int
    value: bytes


@dataclass
class DataWindow:
    start_ms: int
    end_ms: int
    eeg: list[RawPacket] = field(default_factory=list)
    hr_raw: list[RawPacket] = field(default_factory=list)
    native_hr: list[RawPacket] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def append(self, packet: RawPacket) -> None:
        target, expected, reason = {
            "ff31": (self.eeg, EEG_PACKET_BYTES, "EEG_PACKET_LENGTH_INVALID"),
            "ff52": (self.hr_raw, HR_RAW_PACKET_BYTES, "HR_RAW_PACKET_LENGTH_INVALID"),
            "ff51": (self.native_hr, NATIVE_HR_PACKET_BYTES, "NATIVE_HR_PACKET_LENGTH_INVALID"),
        }[packet.characteristic]
        target.append(packet)
        if len(packet.value) != expected and reason not in self.reasons:
            self.reasons.append(reason)

    @staticmethod
    def _payload(packets: list[RawPacket], packet_bytes: int, start_ms: int, end_ms: int) -> dict:
        raw = b"".join(item.value for item in packets)
        return {
            "encoding": "base64", "sampleFormat": "bytes", "packetBytes": packet_bytes,
            "packetCount": len(packets), "byteLength": len(raw), "windowStartMs": start_ms,
            "windowEndMs": end_ms, "bytesBase64": base64.b64encode(raw).decode("ascii"),
        }

    def raw_payload(self) -> dict:
        payload = {}
        if self.eeg:
            payload["eegRaw"] = self._payload(self.eeg, EEG_PACKET_BYTES, self.start_ms, self.end_ms)
        if self.hr_raw:
            payload["hrRaw"] = self._payload(self.hr_raw, HR_RAW_PACKET_BYTES, self.start_ms, self.end_ms)
        # FF51 is persisted for input verification, but is deliberately not a v0.1
        # northbound stream and is never added by Gateway.filtered_payload().
        if self.native_hr:
            payload["nativeHr"] = self._payload(self.native_hr, NATIVE_HR_PACKET_BYTES, self.start_ms, self.end_ms)
        return payload


class WindowAssembler:
    """Groups received bytes without parsing or changing their byte order."""
    def __init__(self, interval_ms: int = 600) -> None:
        self.interval_ms = interval_ms
        self._window: DataWindow | None = None

    def add(self, characteristic: str, value: bytes, received_ms: int | None = None) -> list[DataWindow]:
        received_ms = received_ms or int(time.time() * 1000)
        ready = self.flush_until(received_ms)
        if self._window is None:
            start = received_ms - received_ms % self.interval_ms
            self._window = DataWindow(start, start + self.interval_ms)
        self._window.append(RawPacket(characteristic, received_ms, bytes(value)))
        return ready

    def flush_until(self, timestamp_ms: int) -> list[DataWindow]:
        ready: list[DataWindow] = []
        while self._window is not None and timestamp_ms >= self._window.end_ms:
            finished = self._window
            self._window = DataWindow(finished.end_ms, finished.end_ms + self.interval_ms)
            if finished.eeg or finished.hr_raw or finished.native_hr:
                ready.append(finished)
        return ready

    def flush(self) -> DataWindow | None:
        window, self._window = self._window, None
        return window

    @property
    def window_end_ms(self) -> int | None:
        return self._window.end_ms if self._window else None
