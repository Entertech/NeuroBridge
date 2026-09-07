from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    recording_session_id: str
    timestamp_ms: int
    payload: Mapping[str, object]
    valid: bool
    invalid_reasons: tuple[str, ...] = ()


class ReplayReader(Protocol):
    def events(self, recording_session_id: str) -> AsyncIterator[ReplayEvent]: ...
