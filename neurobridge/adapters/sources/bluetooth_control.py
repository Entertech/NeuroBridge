"""Session-bound BLE capture control; the Source owns the connection."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ...ports.device_control import ControlResult


class BluetoothSessionControl:
    def __init__(
        self,
        current_session_id: Callable[[], str | None],
        existing_stream: Callable[[], bool],
        write: Callable[[bytes], Awaitable[None]],
    ) -> None:
        self._current_session_id = current_session_id
        self._existing_stream = existing_stream
        self._write = write
        self._started_session: str | None = None
        self._stopped_session: str | None = None
        self._lock = asyncio.Lock()

    async def start_stream(self, connection_session_id: str) -> ControlResult:
        async with self._lock:
            if self._current_session_id() != connection_session_id:
                return ControlResult("staleSession", connection_session_id, False, False, "STALE_SESSION")
            if self._existing_stream():
                # Adopt the already-running stream so normal session shutdown can
                # still send exactly one stop while continuing to skip start.
                self._started_session = connection_session_id
                return ControlResult("alreadyStreaming", connection_session_id, False)
            if connection_session_id == self._started_session:
                return ControlResult("alreadyStreaming", connection_session_id, False)
            try:
                await self._write(b"\x05")
            except Exception as error:
                return ControlResult("writeFailed", connection_session_id, False, True, type(error).__name__)
            self._started_session = connection_session_id
            return ControlResult("started", connection_session_id, True)

    async def stop_stream(self, connection_session_id: str) -> ControlResult:
        async with self._lock:
            if self._current_session_id() != connection_session_id:
                return ControlResult("staleSession", connection_session_id, False, False, "STALE_SESSION")
            if connection_session_id == self._stopped_session or connection_session_id != self._started_session:
                return ControlResult("alreadyStopped", connection_session_id, False)
            # Claim the only stop attempt before awaiting the write.
            self._stopped_session = connection_session_id
            try:
                await self._write(b"\x06")
            except Exception as error:
                return ControlResult("writeFailed", connection_session_id, False, True, type(error).__name__)
            return ControlResult("stopped", connection_session_id, True)

    def is_streaming(self, connection_session_id: str) -> bool:
        return connection_session_id == self._current_session_id() == self._started_session and connection_session_id != self._stopped_session
