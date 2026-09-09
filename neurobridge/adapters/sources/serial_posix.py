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
    """Expose unmodified serial read boundaries, including partial frames/noise.

    The legacy adapter remains the physical-session owner during the incremental
    migration. Its EEG/HR compatibility projections are deliberately filtered
    out here; the bound HeadsetRev181Parser owns business payload interpretation.
    """

    def __init__(
        self,
        config: SerialConfig,
        device_ready,
        error=None,
        *,
        queue_size: int = 64,
        external_control: bool = False,
        application_control: bool = False,
        enqueue_timeout_ms: int = 50,
        candidate_provider=None,
        serial_factory=None,
        identity_provider=None,
    ) -> None:
        self._chunk_queue: asyncio.Queue[RawChunk | None] = asyncio.Queue(maxsize=queue_size)
        self._event_queue: asyncio.Queue[DeviceConnectionEvent | None] = asyncio.Queue(maxsize=queue_size)
        self._session_id: str | None = None
        self._status = SourceStatus(ConnectionState.DISCONNECTED)
        self._task: asyncio.Task[None] | None = None
        self._device_ready = device_ready
        self._existing_stream = False
        self._control = None
        self.application_control = application_control
        self.enqueue_timeout_ms = enqueue_timeout_ms
        self.dropped_raw_chunks = 0
        self.dropped_raw_bytes = 0
        self._pending_gap_bytes = 0
        self._adapter = SerialAdapter(
            config,
            self._compatibility_packet,
            self._status_changed,
            device_ready,
            error,
            raw_chunk=self._raw_chunk,
            external_control=external_control,
            external_start=self._start_stream if external_control else None,
            external_stop=self._stop_stream if external_control else None,
            **({"candidate_provider": candidate_provider} if candidate_provider is not None else {}),
            **({"serial_factory": serial_factory} if serial_factory is not None else {}),
            **({"identity_provider": identity_provider} if identity_provider is not None else {}),
        )
        if external_control:
            from .serial_control import SerialSessionControl

            self._control = SerialSessionControl(
                lambda: self._session_id,
                lambda: self._existing_stream,
                self._adapter.control_write,
            )

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._adapter.run())

    async def stop(self) -> None:
        await self._adapter.stop()
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._finish_queue(self._chunk_queue)
        self._finish_queue(self._event_queue)

    def status(self) -> SourceStatus:
        return self._status

    @property
    def existing_stream(self) -> bool:
        return self._existing_stream

    @property
    def control(self):
        return self._control

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
            self._pending_gap_bytes,
        )
        try:
            await asyncio.wait_for(self._chunk_queue.put(value), timeout=self.enqueue_timeout_ms / 1000)
            self._pending_gap_bytes = 0
        except TimeoutError:
            self.dropped_raw_chunks += 1
            self.dropped_raw_bytes += len(data)
            self._pending_gap_bytes += len(data)
            LOG.error(
                "Serial RawChunk queue full: connectionSessionId=%s droppedChunks=%s droppedBytes=%s chunkBytes=%s",
                self._session_id,
                self.dropped_raw_chunks,
                self.dropped_raw_bytes,
                len(data),
            )

    async def _start_stream(self, existing_stream: bool) -> bool:
        if self._control is None or self._session_id is None:
            return False
        self._existing_stream = existing_stream
        if self.application_control:
            # Existing-stream adoption precedes preparation; Application alone
            # invokes DeviceControl after preparing its own session.
            return existing_stream or self._control.is_streaming(self._session_id)
        result = await self._control.start_stream(self._session_id)
        accepted = result.outcome in {"started", "alreadyStreaming"}
        if not accepted:
            self._existing_stream = False
        return accepted

    async def _stop_stream(self) -> None:
        if self._control is None or self._session_id is None:
            return
        result = await self._control.stop_stream(self._session_id)
        if result.outcome == "writeFailed":
            LOG.error(
                "Session-bound serial stop failed: connectionSessionId=%s reason=%s",
                self._session_id,
                result.reason,
            )
        self._existing_stream = False

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
        if str(value) == "connecting":
            await self._emit_state(ConnectionState.DISCOVERING)
            state = ConnectionState.CONNECTING
        await self._emit_state(state)

    async def _emit_state(self, state: ConnectionState) -> None:
        previous_state = self._status.state
        previous_session = self._status.connection_session_id
        if state == ConnectionState.CONNECTED:
            self._pending_gap_bytes = 0
            self._session_id = f"conn-{uuid.uuid4().hex}"
            self._existing_stream = self._adapter._capture_started
        elif state != ConnectionState.VALIDATING:
            self._session_id = None
        event = DeviceConnectionEvent(state, int(time.time() * 1000), self._session_id, self._existing_stream if state == ConnectionState.CONNECTED else False)
        self._status = SourceStatus(state, self._session_id)
        LOG.info(
            "Serial connection state changed: previousState=%s state=%s previousConnectionSessionId=%s "
            "connectionSessionId=%s existingStream=%s eventTimestampMs=%s",
            previous_state.value,
            state.value,
            previous_session,
            self._session_id,
            event.existing_stream,
            event.occurred_at_ms,
        )
        await self._event_queue.put(event)

    @staticmethod
    def _finish_queue(queue: asyncio.Queue[object]) -> None:
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            queue.get_nowait()
            queue.put_nowait(None)
