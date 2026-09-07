"""Latest-value fanout that cannot backpressure acquisition."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ..domain.result import WindowResult


@dataclass(slots=True)
class _ConnectionSlots:
    streams: frozenset[str]
    pending: dict[str, WindowResult] = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class SubscriptionFanout:
    def __init__(self) -> None:
        self._connections: dict[str, _ConnectionSlots] = {}
        self.snapshot_overwrite_count = 0

    def subscribe(self, connection_id: str, streams: frozenset[str]) -> None:
        self._connections[connection_id] = _ConnectionSlots(frozenset(streams))

    def unsubscribe(self, connection_id: str) -> None:
        self._connections.pop(connection_id, None)

    def publish(self, result: WindowResult) -> None:
        present = frozenset(signal.signal_type for signal in result.batch.signals)
        for slots in self._connections.values():
            for stream in slots.streams & present:
                if stream in slots.pending:
                    self.snapshot_overwrite_count += 1
                slots.pending[stream] = result
            if slots.pending:
                slots.changed.set()

    async def next(self, connection_id: str) -> dict[str, WindowResult]:
        slots = self._connections[connection_id]
        await slots.changed.wait()
        values = dict(slots.pending)
        slots.pending.clear()
        slots.changed.clear()
        return values
