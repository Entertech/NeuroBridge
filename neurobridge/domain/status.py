"""Independent device-connection and data-pipeline state models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    DISCOVERING = "discovering"
    CONNECTING = "connecting"
    VALIDATING = "validating"
    CONNECTED = "connected"
    VALIDATION_FAILED = "validation_failed"
    RECONNECTING = "reconnecting"


class DataState(StrEnum):
    UNAVAILABLE = "unavailable"
    PREPARING = "preparing"
    READY = "ready"
    STREAMING = "streaming"
    STALE = "stale"
    ERROR = "error"


class StorageState(StrEnum):
    OK = "ok"
    WARNING = "warning"
    FULL = "full"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DeviceConnectionEvent:
    state: ConnectionState
    occurred_at_ms: int
    connection_session_id: str | None = None
    existing_stream: bool = False
    reason: str | None = None
    retryable: bool = True


@dataclass(frozen=True, slots=True)
class DataStatusSnapshot:
    data_state: DataState = DataState.UNAVAILABLE
    algorithm_state: str = "unavailable"
    storage_state: StorageState = StorageState.OK
    last_produced_at_ms: int | None = None
    last_published_at_ms: int | None = None
    persistence_guaranteed: bool = True
    affected_from_ms: int | None = None
    recent_error: str | None = None
    error_stage: str | None = None
    counters: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "counters", dict(self.counters))
