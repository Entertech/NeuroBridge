"""Session-bound write-only E1/E0 control for serial transports."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ...ports.device_control import ControlResult


class SerialSessionControl:
    def __init__(
        self,
        current_session_id: Callable[[], str | None],
        existing_stream: Callable[[], bool],
        write: Callable[[bytes], Awaitable[None]],
    ) -> None:
        self._current_session_id = current_session_id
        self._existing_stream = existing_stream
        self._write = write
        self._started_sessions: set[str] = set()
        self._stopped_sessions: set[str] = set()
        self._lock = asyncio.Lock()

    async def start_stream(self, connection_session_id: str) -> ControlResult:
        async with self._lock:
            if self._current_session_id() != connection_session_id:
                return ControlResult("staleSession", connection_session_id, False, False, "STALE_SESSION")
            if self._existing_stream() or connection_session_id in self._started_sessions:
                return ControlResult("alreadyStreaming", connection_session_id, False)
            try:
                await self._write(b"\xE1")
            except Exception as error:
                return ControlResult("writeFailed", connection_session_id, False, True, type(error).__name__)
            self._started_sessions.add(connection_session_id)
            return ControlResult("started", connection_session_id, True)

    async def stop_stream(self, connection_session_id: str) -> ControlResult:
        async with self._lock:
            if self._current_session_id() != connection_session_id:
                return ControlResult("staleSession", connection_session_id, False, False, "STALE_SESSION")
            if connection_session_id in self._stopped_sessions or connection_session_id not in self._started_sessions:
                return ControlResult("alreadyStopped", connection_session_id, False)
            # Claim the only E0 attempt before awaiting the write.
            self._stopped_sessions.add(connection_session_id)
            try:
                await self._write(b"\xE0")
            except Exception as error:
                return ControlResult("writeFailed", connection_session_id, False, True, type(error).__name__)
            return ControlResult("stopped", connection_session_id, True)
