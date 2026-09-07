from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

from ..domain.status import StorageState


@dataclass(frozen=True, slots=True)
class PersistenceRecord:
    schema_version: int
    recording_session_id: str
    record_type: str
    captured_at_ms: int
    correlation_id: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, slots=True)
class PersistenceReceipt:
    accepted: bool
    persistence_guaranteed: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class StorageStatus:
    state: StorageState
    available_bytes: int | None = None
    persistence_guaranteed: bool = True
    affected_from_ms: int | None = None
    last_success_at_ms: int | None = None
    gap_count: int = 0
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", dict(self.details))


class RecordingRepository(Protocol):
    def try_append(self, record: PersistenceRecord) -> PersistenceReceipt: ...
    async def close_session(self, recording_session_id: str) -> None: ...
    def storage_status(self) -> StorageStatus: ...
