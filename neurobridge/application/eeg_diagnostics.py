"""Bounded raw EEG diagnostics for the Windows serial headset."""

from __future__ import annotations

import logging
import time

from ..adapters.parsers.headset_rev181 import EEG_END, EEG_START
from ..domain.signal import ParsedSignalBatch


LOG = logging.getLogger(__name__)
ADC_MAX = (1 << 24) - 1
ADC_RAIL_MARGIN = 10  # Match the SDK's EEG load check.
_LOG_INTERVAL_SECONDS = 10


class HeadsetEegDiagnostics:
    def __init__(self) -> None:
        self._session: str | None = None
        self._last_logged: dict[str, float] = {}
        self._previous_pattern: str | None = None

    def observe(self, batch: ParsedSignalBatch) -> None:
        if batch.connection_session_id != self._session:
            self._session = batch.connection_session_id
            self._last_logged.clear()
            self._previous_pattern = None

        packets = [
            signal.samples for signal in batch.signals
            if signal.signal_type == "eeg" and isinstance(signal.samples, bytes)
            and len(signal.samples) == EEG_END - EEG_START
        ]
        if not packets:
            return

        counts = []
        for channel_offset in (0, 3):
            values = [int.from_bytes(packet[offset:offset + 3], "big")
                      for packet in packets for offset in range(channel_offset, len(packet), 6)]
            counts.append({
                "samples": len(values),
                "zero": values.count(0),
                "ff": values.count(ADC_MAX),
                "nearLow": sum(0 < value <= ADC_RAIL_MARGIN for value in values),
                "nearHigh": sum(ADC_MAX - ADC_RAIL_MARGIN <= value < ADC_MAX for value in values),
            })
        if all(channel["zero"] == channel["samples"] for channel in counts):
            pattern = "all_zero"
        elif all(channel["ff"] == channel["samples"] for channel in counts):
            pattern = "all_ff"
        elif any(channel["zero"] or channel["ff"] or channel["nearLow"] or channel["nearHigh"] for channel in counts):
            pattern = "near_adc_rail"
        else:
            pattern = "within_adc_range"
        previous, self._previous_pattern = self._previous_pattern, pattern
        if pattern == "within_adc_range" and previous in (None, pattern):
            return

        now = time.monotonic()
        last = self._last_logged.get(pattern)
        if last is not None and now - last < _LOG_INTERVAL_SECONDS:
            return
        self._last_logged[pattern] = now
        LOG.info(
            "Headset raw EEG: source=headset batchId=%s connectionSessionId=%s windowStartMs=%s windowEndMs=%s "
            "pattern=%s previousPattern=%s batchValid=%s eegPackets=%s hrPackets=%s left=%s right=%s",
            batch.batch_id, batch.connection_session_id, batch.window_start_ms, batch.window_end_ms,
            pattern, previous, batch.valid, len(packets), sum(signal.signal_type == "hr" for signal in batch.signals),
            counts[0], counts[1],
        )
