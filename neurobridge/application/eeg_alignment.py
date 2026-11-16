"""Bounded Windows dual-device EEG input calibration."""

from __future__ import annotations

import asyncio
from collections import Counter, OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
import logging
from statistics import median
import time

from ..adapters.parsers.headset_rev181 import EEG_END, EEG_START, FRAME_BYTES
from ..domain.raw import DeviceFrame, RawChunk
from ..domain.signal import ParsedSignalBatch


LOG = logging.getLogger(__name__)
_PACKET_BYTES = 20
_SEQUENCE_BYTES = 2
_SAMPLE_BYTES = 3
_SAMPLES_PER_CHANNEL = 3
_ADC_MAX = (1 << 24) - 1
_RAIL_MARGIN = 10  # Preserve the native SDK's near-rail wear detection.
_LOG_INTERVAL_SECONDS = 10


@dataclass(frozen=True, slots=True)
class _Reference:
    batch_id: str
    packet_count: int
    reason: str
    amplitude: float | None = None


@dataclass(slots=True)
class _RawStream:
    session: str
    started_ns: int
    started_at_ms: int
    last_ns: int

    def contains(self, batch: ParsedSignalBatch) -> bool:
        return all(signal.received_at_ms >= self.started_at_ms
                   for signal in batch.signals if signal.signal_type == "eeg")


def _samples(raw: bytes) -> list[int]:
    return [int.from_bytes(raw[i:i + _SAMPLE_BYTES], "big")
            for packet in range(0, len(raw), _PACKET_BYTES)
            for i in range(packet + _SEQUENCE_BYTES, packet + _PACKET_BYTES, _SAMPLE_BYTES)]


def _amplitude(samples: list[int], center: float) -> float:
    return median(abs(sample - center) for sample in samples)


def _usable(samples: list[int]) -> bool:
    return bool(samples) and all(_RAIL_MARGIN < sample < _ADC_MAX - _RAIL_MARGIN for sample in samples)


class EegAlignmentCache:
    def __init__(self, *, percent: int, wait_ms: int, raw_timeout_ms: int, max_windows: int,
                 headset_session: Callable[[], str | None], headband_session: Callable[[], str | None]) -> None:
        self._limit = percent / 100
        self._wait_seconds = wait_ms / 1000
        self._raw_timeout_ns = raw_timeout_ms * 1_000_000
        self._max_windows = max_windows
        self._headset_session = headset_session
        self._headband_session = headband_session
        self._streams: dict[str, _RawStream] = {}
        self._references: OrderedDict[tuple[str, int, int, int], _Reference] = OrderedDict()
        self._updated = asyncio.Event()
        self._decisions: Counter[str] = Counter()
        self._last_log_at = time.monotonic()

    def observe_raw_eeg(self, chunk: RawChunk) -> None:
        stream = self._streams.get(chunk.source_type)
        received_ns = chunk.received_at_monotonic_ns
        if (stream is None or stream.session != chunk.connection_session_id
                or received_ns - stream.last_ns >= self._raw_timeout_ns):
            self._streams[chunk.source_type] = _RawStream(
                chunk.connection_session_id, received_ns, chunk.received_at_ms, received_ns)
            self._updated.set()
        else:
            stream.last_ns = max(stream.last_ns, received_ns)

    def _live_stream(self, source: str, session: str | None) -> _RawStream | None:
        stream = self._streams.get(source)
        if (stream is not None and stream.session == session
                and time.monotonic_ns() - stream.last_ns < self._raw_timeout_ns):
            return stream
        return None

    def observe_headband(self, batch: ParsedSignalBatch) -> None:
        stream = self._live_stream("bluetooth", self._headband_session())
        if stream is None or stream.session != batch.connection_session_id or not stream.contains(batch):
            return
        chunks = [signal.samples for signal in batch.signals if signal.signal_type == "eeg"]
        reference = _Reference(batch.batch_id, len(chunks), "headband_no_eeg")
        if not batch.valid:
            reference = replace(reference, reason="headband_invalid_window")
        elif chunks and any(not isinstance(chunk, bytes) or len(chunk) != _PACKET_BYTES for chunk in chunks):
            reference = replace(reference, reason="headband_invalid_packet")
        elif chunks:
            samples = _samples(b"".join(chunks))
            if _usable(samples):
                channels = (samples[::2], samples[1::2])
                amplitude = median(_amplitude(channel, median(channel)) for channel in channels)
                reference = replace(reference, reason="matched" if amplitude else "headband_flat_eeg",
                                    amplitude=amplitude or None)
            else:
                reference = replace(reference, reason="headband_near_adc_rail")
        key = (stream.session, stream.started_ns, batch.window_start_ms, batch.window_end_ms)
        self._references[key] = reference
        self._references.move_to_end(key)
        while len(self._references) > self._max_windows:
            self._references.popitem(last=False)
        self._updated.set()

    async def prepare_headset(self, batch: ParsedSignalBatch, frames: tuple[DeviceFrame, ...]) -> tuple[DeviceFrame, ...]:
        started = time.monotonic()
        session = self._headband_session()
        reference = None
        wait_ms = 0

        def use_original(reason: str) -> tuple[DeviceFrame, ...]:
            self._log_decision(batch, len(frames), reason, session, reference, wait_ms, started)
            return frames

        if not batch.valid:
            return use_original("headset_invalid_window")
        if session is None:
            return use_original("headband_offline")
        if not frames:
            return use_original("headset_no_eeg")
        if any(len(frame.raw_bytes) != FRAME_BYTES for frame in frames):
            return use_original("headset_invalid_packet")
        raw = b"".join(frame.raw_bytes[EEG_START - _SEQUENCE_BYTES:EEG_END] for frame in frames)
        samples = _samples(raw)
        if not _usable(samples):
            return use_original("headset_near_adc_rail")
        headset_stream = self._live_stream("serial", self._headset_session())
        if headset_stream is None or headset_stream.session != batch.connection_session_id:
            return use_original("headset_raw_inactive")
        if not headset_stream.contains(batch):
            return use_original("headset_window_crosses_raw_gap")
        headband_stream = self._live_stream("bluetooth", session)
        if headband_stream is None:
            return use_original("headband_raw_inactive")
        key = (session, headband_stream.started_ns, batch.window_start_ms, batch.window_end_ms)
        wait_started = time.monotonic()

        def stream_failure() -> str | None:
            if self._live_stream("serial", self._headset_session()) is not headset_stream:
                return "headset_raw_inactive"
            if session != self._headband_session():
                return "headband_session_changed"
            if self._live_stream("bluetooth", session) is not headband_stream:
                return "headband_raw_inactive"
            return None

        try:
            async with asyncio.timeout(self._wait_seconds):
                while key not in self._references and stream_failure() is None:
                    self._updated.clear()
                    await self._updated.wait()
        except TimeoutError:
            pass
        wait_ms = round((time.monotonic() - wait_started) * 1000)
        if reason := stream_failure():
            return use_original(reason)
        reference = self._references.get(key)
        if reference is None:
            return use_original("reference_timeout")
        amplitude = reference.amplitude
        if amplitude is None:
            return use_original(reference.reason)

        corrected = bytearray(raw)
        gains = [1.0, 1.0]
        for channel in range(2):
            channel_samples = samples[channel::2]
            center = median(channel_samples)
            original = _amplitude(channel_samples, center)
            if original == 0:
                continue
            gain = min(1 + self._limit, max(1 - self._limit, amplitude / original))
            gains[channel] = gain
            for index, sample in enumerate(channel_samples):
                adjusted = round(center + (sample - center) * gain)
                if not _RAIL_MARGIN < adjusted < _ADC_MAX - _RAIL_MARGIN:
                    return use_original("adjustment_near_adc_rail")
                packet, position = divmod(index, _SAMPLES_PER_CHANNEL)
                offset = packet * _PACKET_BYTES + _SEQUENCE_BYTES + (position * 2 + channel) * _SAMPLE_BYTES
                corrected[offset:offset + _SAMPLE_BYTES] = adjusted.to_bytes(_SAMPLE_BYTES, "big")
        if corrected == raw:
            return use_original("matched_no_change")
        self._log_decision(batch, len(frames), "applied", session, reference, wait_ms, started, tuple(gains))
        # Keep the captured frame immutable. Only the EEG bytes in the working copy change.
        return tuple(
            replace(frame, raw_bytes=frame.raw_bytes[:EEG_START]
                    + corrected[offset + _SEQUENCE_BYTES:offset + _PACKET_BYTES] + frame.raw_bytes[EEG_END:])
            for frame, offset in zip(frames, range(0, len(raw), _PACKET_BYTES), strict=True)
        )

    def _log_decision(self, batch: ParsedSignalBatch, packet_count: int, reason: str, session: str | None,
                      reference: _Reference | None, wait_ms: int, started: float,
                      gains: tuple[float, ...] | None = None) -> None:
        now = time.monotonic()
        if now - self._last_log_at >= _LOG_INTERVAL_SECONDS:
            if self._decisions:
                LOG.info("Headset EEG alignment summary: windowsByReason=%s", dict(self._decisions))
            self._decisions.clear()
            self._last_log_at = now
        level = logging.INFO if not self._decisions[reason] else logging.DEBUG
        self._decisions[reason] += 1
        # Log representative windows at INFO and keep the full trace at DEBUG.
        LOG.log(level,
                "Headset EEG alignment decision: batchId=%s headsetSession=%s headbandSession=%s "
                "referenceBatchId=%s windowStartMs=%s windowEndMs=%s reason=%s headsetPackets=%s "
                "headbandPackets=%s waitedMs=%s durationMs=%s gains=%s",
                batch.batch_id, batch.connection_session_id, session, reference.batch_id if reference else None,
                batch.window_start_ms, batch.window_end_ms, reason, packet_count,
                reference.packet_count if reference else None, wait_ms, round((now - started) * 1000),
                tuple(round(gain, 4) for gain in gains) if gains else None)
