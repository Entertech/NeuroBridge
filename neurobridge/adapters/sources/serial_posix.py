"""RawDataSource facade for the current POSIX USB-TTY adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
import time
import uuid

from ...config import SerialConfig
from ...device.packet import DevicePacket
from ...domain.raw import RawChunk
from ...domain.status import ConnectionState, DeviceConnectionEvent
from ...ports.raw_source import SourceStatus
from ...serial.adapter import SerialAdapter

LOG = logging.getLogger(__name__)


class PosixSerialSource:
    """Expose only complete, unmodified rev-181 frames at the raw-source port.

    The legacy adapter remains the physical-session owner during the incremental
    migration. Its EEG/HR compatibility projections are deliberately filtered
    out here; the bound HeadsetRev181Parser recreates them from the full frame.
    """

    def __init__(self, config: SerialConfig, device_ready, error=None, *, queue_size: int = 64) -> None:
        self._chunk_queue: asyncio.Queue[RawChunk | None] = asyncio.Queue(maxsize=queue_size)
        self._event_queue: asyncio.Queue[DeviceConnectionEvent | None] = asyncio.Queue(maxsize=queue_size)
        self._session_id: str | None = None
        self._status = SourceStatus(ConnectionState.DISCONNECTED)
        self._task: asyncio.Task[None] | None = None
        self.dropped_raw_chunks = 0
        self.dropped_raw_bytes = 0
        self._adapter = SerialAdapter(
            config,
            self._compatibility_packet,
            self._status_changed,
            device_ready,
            error,
            raw_chunk=self._raw_chunk,
        )

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._adapter.run())

    async def stop(self) -> None:
        await self._adapter.stop()
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self._chunk_queue.put(None)
        await self._event_queue.put(None)

    def status(self) -> SourceStatus:
        return self._status

    async def chunks(self) -> AsyncIterator[RawChunk]:
        while (item := await self._chunk_queue.get()) is not None:
            yield item

    async def connection_events(self) -> AsyncIterator[DeviceConnectionEvent]:
        while (item := await self._event_queue.get()) is not None:
            yield item

    async def _compatibility_packet(self, packet: DevicePacket) -> None:
        """The external Parser owns payload interpretation on this facade."""

    async def _raw_chunk(self, data: bytes, received_at_ms: int) -> None:
        if self._session_id is None:
            return
        value = RawChunk(
            "serial",
            "serial",
            data,
            received_at_ms,
            time.monotonic_ns(),
            self._session_id,
            f"trace-{uuid.uuid4().hex}",
        )
        try:
            await asyncio.wait_for(self._chunk_queue.put(value), timeout=0.05)
        except TimeoutError:
            self.dropped_raw_chunks += 1
            self.dropped_raw_bytes += len(data)
            LOG.error(
                "Serial RawChunk queue full: connectionSessionId=%s droppedChunks=%s droppedBytes=%s chunkBytes=%s",
                self._session_id,
                self.dropped_raw_chunks,
                self.dropped_raw_bytes,
                len(data),
            )

    async def _status_changed(self, name: str, value: object) -> None:
        if name != "connectionState":
            return
        mapping = {
            "not_connected": ConnectionState.RECONNECTING,
            "connecting": ConnectionState.DISCOVERING,
            "validating": ConnectionState.VALIDATING,
            "validated": ConnectionState.CONNECTED,
            "validation_failed": ConnectionState.VALIDATION_FAILED,
            "disconnected": ConnectionState.RECONNECTING,
        }
        state = mapping.get(str(value), ConnectionState.DISCONNECTED)
        if state == ConnectionState.CONNECTED:
            self._session_id = f"conn-{uuid.uuid4().hex}"
        elif state != ConnectionState.VALIDATING:
            self._session_id = None
        event = DeviceConnectionEvent(state, int(time.time() * 1000), self._session_id)
        self._status = SourceStatus(state, self._session_id)
        await self._event_queue.put(event)
