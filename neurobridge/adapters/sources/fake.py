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
        self._stopped = asyncio.Event()
        self._started = False

    async def start(self) -> None:
        self._stopped.clear()
        self._started = True

    async def wait_stopped(self) -> None:
        await self._stopped.wait()

    async def stop(self, *, drain=False) -> None:
        self._stopped.set()
        self._started = False
        if drain:
            await self._chunks.join()
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
        while True:
            item = await self._chunks.get()
            try:
                if item is None:
                    return
                yield item
            finally:
                self._chunks.task_done()

    async def connection_events(self) -> AsyncIterator[DeviceConnectionEvent]:
        while (item := await self._events.get()) is not None:
            yield item
