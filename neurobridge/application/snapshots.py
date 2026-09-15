"""Atomic latest-window snapshot storage."""

from __future__ import annotations

from threading import RLock

from ..domain.result import WindowResult
from ..ports.northbound import SnapshotRead, SnapshotVersion


class InMemoryLatestSnapshotStore:
    """Replace every stream from one WindowResult under a single lock."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._counter = 0
        self._latest: WindowResult | None = None
        self._version: SnapshotVersion | None = None

    def replace(self, result: WindowResult) -> SnapshotVersion:
        with self._lock:
            self._counter += 1
            self._latest = result
            self._version = SnapshotVersion(self._counter, result.batch.batch_id)
            return self._version

    def clear(self) -> None:
        with self._lock:
            self._latest = None
            self._version = None

    def get(self, streams: frozenset[str]) -> SnapshotRead:
        with self._lock:
            return SnapshotRead(self._version, self._latest, frozenset(streams))
