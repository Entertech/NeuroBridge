from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ControlResult:
    outcome: str
    connection_session_id: str
    command_sent: bool
    retryable: bool = False
    reason: str | None = None


class DeviceControl(Protocol):
    async def start_stream(self, connection_session_id: str) -> ControlResult: ...
    async def stop_stream(self, connection_session_id: str) -> ControlResult: ...
