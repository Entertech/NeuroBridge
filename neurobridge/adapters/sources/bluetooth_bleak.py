"""RawDataSource facade for the current Bleak headband adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import time
import uuid

from ...ble.flowtime import FlowtimeAdapter
from ...config import BleConfig
from ...device.packet import DevicePacket
from ...domain.raw import RawChunk
from ...domain.status import ConnectionState, DeviceConnectionEvent
from ...ports.raw_source import SourceStatus


class BluetoothBleakSource:
    def __init__(self, config: BleConfig, device_ready, *, queue_size: int = 64) -> None:
        self._chunks: asyncio.Queue[RawChunk | None] = asyncio.Queue(maxsize=queue_size)
        self._events: asyncio.Queue[DeviceConnectionEvent | None] = asyncio.Queue(maxsize=queue_size)
        self._session_id: str | None = None
        self._status = SourceStatus(ConnectionState.DISCONNECTED)
        self._task: asyncio.Task[None] | None = None
        self._adapter = FlowtimeAdapter(config, self._packet, self._status_changed, device_ready)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._adapter.run())

    async def stop(self) -> None:
        await self._adapter.stop()
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self._chunks.put(None)
        await self._events.put(None)

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
        await self._chunks.put(RawChunk("bluetooth", packet.channel, packet.value, packet.received_at_ms, time.monotonic_ns(), self._session_id, f"trace-{uuid.uuid4().hex}"))

    async def _status_changed(self, name: str, value: object) -> None:
        if name != "connectionState":
            return
        state = {"connecting": ConnectionState.CONNECTING, "connected": ConnectionState.CONNECTED}.get(str(value), ConnectionState.RECONNECTING)
        if state == ConnectionState.CONNECTED:
            self._session_id = f"conn-{uuid.uuid4().hex}"
        else:
            self._session_id = None
        event = DeviceConnectionEvent(state, int(time.time() * 1000), self._session_id)
        self._status = SourceStatus(state, self._session_id)
        await self._events.put(event)
