"""Bounded in-memory source used by contract and integration tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ...domain.raw import RawChunk
from ...domain.status import ConnectionState, DeviceConnectionEvent
from ...ports.raw_source import SourceStatus


class FakeRawDataSource:
    def __init__(self, queue_size: int = 16) -> None:
        self._chunks: asyncio.Queue[RawChunk | None] = asyncio.Queue(maxsize=queue_size)
        self._events: asyncio.Queue[DeviceConnectionEvent | None] = asyncio.Queue(maxsize=queue_size)
        self._status = SourceStatus(ConnectionState.DISCONNECTED)
        self._started = False

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False
        await self._chunks.put(None)
        await self._events.put(None)
        self._status = SourceStatus(ConnectionState.DISCONNECTED)

    def status(self) -> SourceStatus:
        return self._status

    async def emit_chunk(self, chunk: RawChunk) -> None:
        if not self._started:
            raise RuntimeError("source is not started")
        await self._chunks.put(chunk)

    async def emit_event(self, event: DeviceConnectionEvent) -> None:
        if not self._started:
            raise RuntimeError("source is not started")
        self._status = SourceStatus(event.state, event.connection_session_id, event.reason)
        await self._events.put(event)

    async def chunks(self) -> AsyncIterator[RawChunk]:
        while (item := await self._chunks.get()) is not None:
            yield item

    async def connection_events(self) -> AsyncIterator[DeviceConnectionEvent]:
        while (item := await self._events.get()) is not None:
            yield item
