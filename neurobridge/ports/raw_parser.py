from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from ..domain.raw import ParseOutcome, RawChunk


class FlushReason(StrEnum):
    DISCONNECTED = "disconnected"
    STOPPED = "stopped"
    RESET = "reset"


class RawDataParser(Protocol):
    def feed(self, chunk: RawChunk) -> ParseOutcome: ...
    def flush(self, reason: FlushReason) -> ParseOutcome: ...
    def reset(self) -> None: ...
