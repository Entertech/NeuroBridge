"""Raw transport and parser boundary models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
import time
import uuid


def wall_clock_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True, slots=True)
class RawChunk:
    source_type: str
    channel: str
    data: bytes
    received_at_ms: int
    received_at_monotonic_ns: int
    connection_session_id: str
    trace_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", bytes(self.data))
        if not self.source_type or not self.channel:
            raise ValueError("RawChunk source_type and channel are required")
        if not self.connection_session_id or not self.trace_id:
            raise ValueError("RawChunk correlation identifiers are required")
        if self.received_at_ms < 0 or self.received_at_monotonic_ns < 0:
            raise ValueError("RawChunk timestamps cannot be negative")

    @classmethod
    def received(
        cls,
        source_type: str,
        channel: str,
        data: bytes,
        connection_session_id: str,
        *,
        trace_id: str | None = None,
    ) -> "RawChunk":
        return cls(
            source_type=source_type,
            channel=channel,
            data=bytes(data),
            received_at_ms=wall_clock_ms(),
            received_at_monotonic_ns=time.monotonic_ns(),
            connection_session_id=connection_session_id,
            trace_id=trace_id or f"trace-{uuid.uuid4().hex}",
        )


@dataclass(frozen=True, slots=True)
class DeviceFrame:
    device_protocol: str
    raw_bytes: bytes
    frame_id: str
    sequence: int | None
    received_at_ms: int
    source_chunk_ids: tuple[str, ...]
    connection_session_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_bytes", bytes(self.raw_bytes))
        object.__setattr__(self, "source_chunk_ids", tuple(self.source_chunk_ids))
        if not self.raw_bytes:
            raise ValueError("DeviceFrame raw_bytes cannot be empty")


@dataclass(frozen=True, slots=True)
class ParseDiagnostic:
    kind: str
    severity: str
    byte_count: int
    occurred_at_ms: int
    sequence_range: tuple[int, int] | None = None
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in {"info", "warning", "error"}:
            raise ValueError("ParseDiagnostic severity is invalid")
        if self.byte_count < 0:
            raise ValueError("ParseDiagnostic byte_count cannot be negative")
        object.__setattr__(self, "details", dict(self.details))


@dataclass(frozen=True, slots=True)
class ParseOutcome:
    frames: tuple[DeviceFrame, ...] = ()
    signals: tuple["ParsedSignal", ...] = ()
    diagnostics: tuple[ParseDiagnostic, ...] = ()
    buffered_bytes: int = 0
    discarded_bytes: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "frames", tuple(self.frames))
        object.__setattr__(self, "signals", tuple(self.signals))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if self.buffered_bytes < 0 or self.discarded_bytes < 0:
            raise ValueError("ParseOutcome byte counts cannot be negative")


from .signal import ParsedSignal  # noqa: E402  (typing cycle only)
