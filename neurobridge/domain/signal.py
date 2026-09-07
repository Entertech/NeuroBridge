"""Transport-neutral parsed signal and window models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ParsedSignal:
    signal_type: str
    samples: bytes | tuple[int | float, ...]
    sample_format: str
    unit: str | None
    window_hint: Mapping[str, object]
    frame_refs: tuple[str, ...]
    received_at_ms: int
    valid: bool = True
    invalid_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.samples, (bytes, bytearray, memoryview)):
            object.__setattr__(self, "samples", bytes(self.samples))
        else:
            object.__setattr__(self, "samples", tuple(self.samples))
        object.__setattr__(self, "window_hint", dict(self.window_hint))
        object.__setattr__(self, "frame_refs", tuple(self.frame_refs))
        object.__setattr__(self, "invalid_reasons", tuple(self.invalid_reasons))
        if not self.valid and not self.invalid_reasons:
            raise ValueError("Invalid ParsedSignal requires an invalid reason")


@dataclass(frozen=True, slots=True)
class ParsedSignalBatch:
    batch_id: str
    device_protocol: str
    connection_session_id: str
    recording_session_id: str
    window_start_ms: int
    window_end_ms: int
    signals: tuple[ParsedSignal, ...]
    frame_refs: tuple[str, ...]
    valid: bool = True
    invalid_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "signals", tuple(self.signals))
        object.__setattr__(self, "frame_refs", tuple(dict.fromkeys(self.frame_refs)))
        object.__setattr__(self, "invalid_reasons", tuple(dict.fromkeys(self.invalid_reasons)))
        if self.window_end_ms < self.window_start_ms:
            raise ValueError("Batch window_end_ms cannot precede window_start_ms")
        if not self.valid and not self.invalid_reasons:
            raise ValueError("Invalid ParsedSignalBatch requires an invalid reason")
