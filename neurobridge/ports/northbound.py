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


class NorthboundCodec(Protocol):
    """Projection boundary; implementations own the concrete wire schema."""
    def envelope(self, code: int, data: dict, message: str = "OK") -> dict: ...
    def filtered_payload(self, raw: dict, algorithm_payload: dict | None, streams: frozenset[str]) -> dict: ...
    def event_data(self, gateway, event: str, subscription_id: str | None, timestamp_ms: int, mode: str, valid: bool, payload: dict) -> dict: ...
    def status_result(self, gateway) -> dict: ...
    def northbound_status(self, gateway) -> dict: ...
    def error(self, request_id: str | None, error) -> dict: ...

    def offline_data_error(self, gateway): ...
