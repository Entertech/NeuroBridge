"""Algorithm port values independent of the native SDK bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class AlgorithmState(StrEnum):
    UNAVAILABLE = "unavailable"
    INITIALIZING = "initializing"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AlgorithmSession:
    connection_session_id: str
    recording_session_id: str
    device_protocol: str


@dataclass(frozen=True, slots=True)
class AlgorithmInput:
    batch_id: str
    payload: Mapping[str, object]
    mapping_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, slots=True)
class AlgorithmResult:
    batch_id: str
    algorithm_version: str | None
    started_at_ms: int
    completed_at_ms: int
    metrics: Mapping[str, object] = field(default_factory=dict)
    valid: bool = True
    invalid_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", dict(self.metrics))
        object.__setattr__(self, "invalid_reasons", tuple(dict.fromkeys(self.invalid_reasons)))
        if self.completed_at_ms < self.started_at_ms:
            raise ValueError("AlgorithmResult completion cannot precede start")
        if not self.valid and not self.invalid_reasons:
            raise ValueError("Invalid AlgorithmResult requires an invalid reason")
