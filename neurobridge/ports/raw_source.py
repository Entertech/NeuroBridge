from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from ..domain.raw import RawChunk
from ..domain.status import ConnectionState, DeviceConnectionEvent


@dataclass(frozen=True, slots=True)
class SourceStatus:
    state: ConnectionState
    connection_session_id: str | None = None
    recent_error: str | None = None


class RawDataSource(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def chunks(self) -> AsyncIterator[RawChunk]: ...
    def connection_events(self) -> AsyncIterator[DeviceConnectionEvent]: ...
    def status(self) -> SourceStatus: ...
