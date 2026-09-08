"""RawDataSource facade for the current Bleak headband adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
import time
import uuid

from ...ble.flowtime import FlowtimeAdapter
from ...config import BleConfig
from ...device.packet import DevicePacket
from ...domain.raw import RawChunk
from ...domain.status import ConnectionState, DeviceConnectionEvent
from ...ports.raw_source import SourceStatus


LOG = logging.getLogger(__name__)


class BluetoothBleakSource:
    def __init__(self, config: BleConfig, device_ready, error=None, *, queue_size: int = 64, enqueue_timeout_ms: int = 50) -> None:
        self._chunks: asyncio.Queue[RawChunk | None] = asyncio.Queue(maxsize=queue_size)
        self._events: asyncio.Queue[DeviceConnectionEvent | None] = asyncio.Queue(maxsize=queue_size)
        self._session_id: str | None = None
        self._status = SourceStatus(ConnectionState.DISCONNECTED)
        self._task: asyncio.Task[None] | None = None
        self._device_ready = device_ready
        self.dropped_raw_chunks = 0
        self.dropped_raw_bytes = 0
        self._pending_gap_bytes = 0
        self.enqueue_timeout_ms = enqueue_timeout_ms
        self._adapter = FlowtimeAdapter(config, self._packet, self._status_changed, self._prepare_device, error)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._adapter.run())

    async def stop(self) -> None:
        await self._adapter.stop()
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._finish_queue(self._chunks)
        self._finish_queue(self._events)

    def status(self) -> SourceStatus:
        return self._status

    async def chunks(self) -> AsyncIterator[RawChunk]:
        while (item := await self._chunks.get()) is not None:
            yield item

    async def connection_events(self) -> AsyncIterator[DeviceConnectionEvent]:
        while (item := await self._events.get()) is not None:
            yield item

    async def _packet(self, packet: DevicePacket) -> None:
        if self._session_id is None:
            return
        value = RawChunk("bluetooth", packet.channel, packet.value, packet.received_at_ms, time.monotonic_ns(), self._session_id, f"trace-{uuid.uuid4().hex}", self._pending_gap_bytes)
        try:
            await asyncio.wait_for(self._chunks.put(value), timeout=self.enqueue_timeout_ms / 1000)
            self._pending_gap_bytes = 0
        except TimeoutError:
            self.dropped_raw_chunks += 1
            self.dropped_raw_bytes += len(packet.value)
            self._pending_gap_bytes += len(packet.value)
            LOG.error(
                "Bluetooth RawChunk queue full: connectionSessionId=%s droppedChunks=%s droppedBytes=%s chunkBytes=%s",
                self._session_id,
                self.dropped_raw_chunks,
                self.dropped_raw_bytes,
                len(packet.value),
            )

    async def _prepare_device(self) -> None:
        # Notifications are installed and the BLE link is usable here. Emit the
        # application connection session before algorithm initialization and the
        # FF21 start command, so no post-start bytes precede recording setup.
        if self._session_id is None:
            self._session_id = f"conn-{uuid.uuid4().hex}"
        if self._status.state != ConnectionState.CONNECTED:
            event = DeviceConnectionEvent(ConnectionState.CONNECTED, int(time.time() * 1000), self._session_id)
            self._status = SourceStatus(ConnectionState.CONNECTED, self._session_id)
            await self._events.put(event)
        await self._device_ready()

    async def _status_changed(self, name: str, value: object) -> None:
        if name != "connectionState":
            return
        state = {"connecting": ConnectionState.CONNECTING, "connected": ConnectionState.CONNECTED}.get(str(value), ConnectionState.RECONNECTING)
        if state == ConnectionState.CONNECTED:
            self._session_id = self._session_id or f"conn-{uuid.uuid4().hex}"
            if self._status.state == ConnectionState.CONNECTED:
                return
        event = DeviceConnectionEvent(state, int(time.time() * 1000), self._session_id)
        self._status = SourceStatus(state, self._session_id)
        await self._events.put(event)
        if state != ConnectionState.CONNECTED:
            self._session_id = None

    @staticmethod
    def _finish_queue(queue: asyncio.Queue[object]) -> None:
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            queue.get_nowait()
            queue.put_nowait(None)
