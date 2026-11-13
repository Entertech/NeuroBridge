"""Bounded Windows dual-device EEG input calibration."""

from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace
import logging
from statistics import median

from ..domain.algorithm import AlgorithmInput
from ..domain.signal import ParsedSignalBatch


LOG = logging.getLogger(__name__)
_PACKET_BYTES = 20
_SEQUENCE_BYTES = 2
_SAMPLE_BYTES = 3
_SAMPLES_PER_CHANNEL = 3
_ADC_MAX = (1 << 24) - 1
_RAIL_MARGIN = 10  # Preserve the native SDK's near-rail wear detection.


def _samples(raw: bytes) -> list[int]:
    return [int.from_bytes(raw[i:i + _SAMPLE_BYTES], "big")
            for packet in range(0, len(raw), _PACKET_BYTES)
            for i in range(packet + _SEQUENCE_BYTES, packet + _PACKET_BYTES, _SAMPLE_BYTES)]


def _amplitude(samples: list[int], center: float) -> float:
    return median(abs(sample - center) for sample in samples)


def _usable(samples: list[int]) -> bool:
    return bool(samples) and all(_RAIL_MARGIN < sample < _ADC_MAX - _RAIL_MARGIN for sample in samples)


class EegAlignmentCache:
    def __init__(self, *, percent: int, wait_ms: int, max_windows: int,
                 headband_session: Callable[[], str | None]) -> None:
        self._limit = percent / 100
        self._wait_seconds = wait_ms / 1000
        self._max_windows = max_windows
        self._headband_session = headband_session
        self._references: OrderedDict[tuple[str, int, int], float | None] = OrderedDict()
        self._updated = asyncio.Event()

    def observe_headband(self, batch: ParsedSignalBatch) -> None:
        if batch.connection_session_id != self._headband_session():
            return
        chunks = [signal.samples for signal in batch.signals if signal.signal_type == "eeg"]
        amplitude = None
        if batch.valid and chunks and all(isinstance(chunk, bytes) and len(chunk) == _PACKET_BYTES for chunk in chunks):
            samples = _samples(b"".join(chunks))
            if _usable(samples):
                channels = (samples[::2], samples[1::2])
                amplitude = median(_amplitude(channel, median(channel)) for channel in channels) or None
        key = (batch.connection_session_id, batch.window_start_ms, batch.window_end_ms)
        self._references[key] = amplitude
        self._references.move_to_end(key)
        while len(self._references) > self._max_windows:
            self._references.popitem(last=False)
        self._updated.set()

    async def prepare_headset(self, batch: ParsedSignalBatch, value: AlgorithmInput) -> AlgorithmInput:
        session = self._headband_session()
        if not batch.valid or session is None:
            return value
        raw = base64.b64decode(str(value.payload["eegRawBase64"]))
        if not raw or len(raw) % _PACKET_BYTES:
            return value
        samples = _samples(raw)
        if not _usable(samples):
            return value
        key = (session, batch.window_start_ms, batch.window_end_ms)
        try:
            async with asyncio.timeout(self._wait_seconds):
                while key not in self._references and session == self._headband_session():
                    self._updated.clear()
                    await self._updated.wait()
        except TimeoutError:
            return value
        amplitude = self._references.get(key)
        if amplitude is None or session != self._headband_session():
            return value

        corrected = bytearray(raw)
        for channel in range(2):
            channel_samples = samples[channel::2]
            center = median(channel_samples)
            original = _amplitude(channel_samples, center)
            if original == 0:
                continue
            gain = min(1 + self._limit, max(1 - self._limit, amplitude / original))
            for index, sample in enumerate(channel_samples):
                adjusted = round(center + (sample - center) * gain)
                if not _RAIL_MARGIN < adjusted < _ADC_MAX - _RAIL_MARGIN:
                    return value
                packet, position = divmod(index, _SAMPLES_PER_CHANNEL)
                offset = packet * _PACKET_BYTES + _SEQUENCE_BYTES + (position * 2 + channel) * _SAMPLE_BYTES
                corrected[offset:offset + _SAMPLE_BYTES] = adjusted.to_bytes(_SAMPLE_BYTES, "big")
        if corrected == raw:
            return value
        LOG.debug("Headset EEG input aligned: batchId=%s windowEndMs=%s", batch.batch_id, batch.window_end_ms)
        return replace(value, mapping_version="2", payload={
            **value.payload, "eegRawBase64": base64.b64encode(corrected).decode("ascii"),
        })
