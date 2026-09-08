from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.result import WindowResult


@dataclass(frozen=True, slots=True)
class SnapshotVersion:
    value: int
    batch_id: str


@dataclass(frozen=True, slots=True)
class SnapshotRead:
    version: SnapshotVersion | None
    result: WindowResult | None
    streams: frozenset[str]


@dataclass(frozen=True, slots=True)
class ApplicationEvent:
    kind: str
    result: WindowResult | None = None
    subscription_id: str | None = None


class LatestSnapshotStore(Protocol):
    def clear(self) -> None: ...
    def replace(self, result: WindowResult) -> SnapshotVersion: ...
    def get(self, streams: frozenset[str]) -> SnapshotRead: ...


class NorthboundSink(Protocol):
    async def publish(self, event: ApplicationEvent) -> None: ...
