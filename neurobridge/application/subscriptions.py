"""Latest-value fanout that cannot backpressure acquisition."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ..domain.result import WindowResult


@dataclass(slots=True)
class _ConnectionSlots:
    streams: frozenset[str]
    pending: dict[str, object] = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class SubscriptionFanout:
    def __init__(self) -> None:
        self._connections: dict[str, _ConnectionSlots] = {}
        self.snapshot_overwrite_count = 0
        self.overwrites_by_stream: dict[str, int] = {}

    def subscribe(self, connection_id: str, streams: frozenset[str]) -> None:
        self._connections[connection_id] = _ConnectionSlots(frozenset(streams))

    def unsubscribe(self, connection_id: str) -> None:
        self._connections.pop(connection_id, None)

    def offer(self, connection_id: str, streams: frozenset[str], value: object) -> int:
        """One slot per requested stream; callers reject overlapping subscriptions."""
        slots = self._connections.setdefault(connection_id, _ConnectionSlots(streams))
        overwritten = 0
        for stream in streams:
            if stream in slots.pending:
                overwritten += 1
                self.overwrites_by_stream[stream] = self.overwrites_by_stream.get(stream, 0) + 1
            slots.pending[stream] = value
        self.snapshot_overwrite_count += overwritten
        if streams:
            slots.changed.set()
        return overwritten

    @property
    def pending_count(self) -> int:
        return sum(len(slots.pending) for slots in self._connections.values())

    def publish(self, result: WindowResult) -> None:
        present = frozenset(signal.signal_type for signal in result.batch.signals)
        for slots in self._connections.values():
            for stream in slots.streams & present:
                if stream in slots.pending:
                    self.snapshot_overwrite_count += 1
                slots.pending[stream] = result
            if slots.pending:
                slots.changed.set()

    async def next(self, connection_id: str) -> dict[str, object]:
        slots = self._connections.get(connection_id)
        if slots is None:
            raise asyncio.CancelledError
        await slots.changed.wait()
        values = dict(slots.pending)
        slots.pending.clear()
        slots.changed.clear()
        return values
