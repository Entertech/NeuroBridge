"""Raw source-to-parser application boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..domain.raw import DeviceFrame, ParseOutcome, RawChunk
from ..domain.signal import ParsedSignal
from ..ports.raw_parser import RawDataParser


class AcquisitionProcessor:
    def __init__(
        self,
        parser: RawDataParser,
        on_frame: Callable[[DeviceFrame], Awaitable[None]],
        on_signal: Callable[[ParsedSignal], Awaitable[None]],
        on_outcome: Callable[[ParseOutcome], Awaitable[None]] | None = None,
    ) -> None:
        self.parser = parser
        self.on_frame = on_frame
        self.on_signal = on_signal
        self.on_outcome = on_outcome

    async def process(self, chunk: RawChunk) -> ParseOutcome:
        outcome = self.parser.feed(chunk)
        for frame in outcome.frames:
            await self.on_frame(frame)
        for signal in outcome.signals:
            await self.on_signal(signal)
        if self.on_outcome is not None:
            await self.on_outcome(outcome)
        return outcome
