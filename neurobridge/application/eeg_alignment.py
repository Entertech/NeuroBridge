"""Calibrate Windows headset EEG outputs against completed headband results."""

from __future__ import annotations

import asyncio
from bisect import bisect_left
from collections import Counter, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from math import isfinite
from statistics import median
import logging
import time

from ..domain.result import WindowResult
from ..domain.signal import ParsedSignalBatch
from .eeg_sample_time import EegPacketClock, EegSampleTime


LOG = logging.getLogger(__name__)
_CHANNELS = ("left", "right")
_SCORES = ("relaxation", "attention", "pleasure")
_BANDS = ("alpha", "beta", "theta", "delta", "gamma", "lowBeta", "highBeta")
_WAVE_LIMIT = 2000  # The native SDK dual-channel waveform limit.
_LOG_INTERVAL_SECONDS = 10


@dataclass(frozen=True, slots=True)
class _Reference:
    batch_id: str
    window_end_ms: int
    positions: tuple[int, ...]
    channels: dict[str, tuple[float, ...]]
    metrics: Mapping[str, object]
    raw_eeg_all_ff: bool


def _fields(values: object) -> Mapping[str, object]:
    return values if isinstance(values, Mapping) else {}


def _usable_wave(samples: object) -> bool:
    if not isinstance(samples, (list, tuple)) or len(samples) < 2 or any(
        type(value) not in (int, float) or not -_WAVE_LIMIT <= value <= _WAVE_LIMIT for value in samples
    ):
        return False
    return samples.count(median(samples)) * 2 <= len(samples)


def _has_all_ff_eeg_packet(batch: ParsedSignalBatch) -> bool:
    # BLE EEG signals retain their two-byte sequence number. Serial EEG signals contain only EEG bytes.
    offset = 2 if batch.device_protocol == "headband_ble" else 0
    for signal in batch.signals:
        if signal.signal_type == "eeg" and signal.valid and isinstance(signal.samples, bytes):
            samples = signal.samples[offset:]
            if samples and samples.count(0xFF) == len(samples):
                return True
    return False


class EegAlignmentCache:
    def __init__(self, *, percent: int, window_ms: int = 600, wait_ms: int = 800,
                 reference_batches: int = 16, clock_packets: int = 4096,
                 clock_ms: Callable[[], int] | None = None) -> None:
        self._ratio = percent / 100
        self._window_ms = window_ms
        self._wait_ms = wait_ms
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._clock_packets = clock_packets
        self._clocks: dict[str, tuple[str, EegPacketClock]] = {}
        self._references: deque[_Reference] = deque(maxlen=reference_batches)
        self._reference_changed = asyncio.Event()
        self._used_reference: dict[str, int] = {}
        self._decisions: Counter[str] = Counter()
        self._last_log_at = time.monotonic()

    def set_session(self, protocol: str, session: str | None) -> None:
        current = self._clocks.get(protocol)
        if current is not None and current[0] == session:
            return
        if session is None:
            self._clocks.pop(protocol, None)
        else:
            self._clocks[protocol] = (session, EegPacketClock(self._window_ms, self._clock_packets))
        if protocol == "headband_ble":
            self._references.clear()
            self._used_reference.clear()
            self._notify_reference_change()

    def _notify_reference_change(self) -> None:
        self._reference_changed.set()
        self._reference_changed = asyncio.Event()

    async def align_result(self, result: WindowResult) -> WindowResult:
        if not self._ratio:
            return result
        batch = result.batch
        active = self._clocks.get(batch.device_protocol)
        if active is None or active[0] != batch.connection_session_id:
            return result
        clock = active[1]
        positions = clock.observe(batch)
        metrics = result.algorithm_result.metrics
        eeg = _fields(metrics.get("eeg"))
        wave = _fields(eeg.get("wave"))
        raw_eeg_all_ff = _has_all_ff_eeg_packet(batch)
        channels = {}
        if result.valid and not raw_eeg_all_ff:
            for channel in _CHANNELS:
                samples = wave.get(channel)
                if _usable_wave(samples):
                    channels[channel] = tuple(samples)

        if batch.device_protocol == "headband_ble":
            self._references.append(_Reference(
                batch.batch_id, batch.window_end_ms, positions, channels, metrics, raw_eeg_all_ff,
            ))
            self._notify_reference_change()
            return result

        if raw_eeg_all_ff:
            reason = "headset_raw_eeg_all_ff"
        elif not result.valid:
            reason = "headset_invalid_output"
        elif not channels:
            reason = "headset_no_usable_waveform"
        else:
            reason = None
        if reason:
            self._log_decision(result, reason, None)
            return result

        headset_time = clock.estimate()
        band_session = self._clocks.get("headband_ble")
        if band_session is None:
            self._log_decision(result, "no_reference_window", None)
            return result
        wait_started = time.monotonic()
        wait_ms = min(self._wait_ms, batch.window_end_ms + self._wait_ms - self._clock_ms())
        if wait_ms > 0:
            try:
                async with asyncio.timeout(wait_ms / 1000):
                    while self._clocks.get("headband_ble") is band_session and not self._reference_covers(
                        batch.window_end_ms, positions, headset_time, band_session[1],
                    ):
                        await self._reference_changed.wait()
            except TimeoutError:
                pass
        waited_ms = round((time.monotonic() - wait_started) * 1000)
        if self._clocks.get(batch.device_protocol) is not active or self._clocks.get("headband_ble") is not band_session:
            return result

        nearby = tuple(ref for ref in self._references if abs(ref.window_end_ms - batch.window_end_ms) <= self._window_ms)
        same_window = tuple(ref for ref in nearby if ref.window_end_ms == batch.window_end_ms)
        reference = next((ref for ref in reversed(same_window) if ref.raw_eeg_all_ff), None)
        if reference is not None:
            self._log_decision(result, "headband_raw_eeg_all_ff", reference, waited_ms=waited_ms)
            return result
        reference = next((ref for ref in reversed(same_window) if ref.channels), None)
        reference_metrics = reference.metrics if reference is not None else {}
        band_time = band_session[1].estimate()

        corrected_wave = {}
        paired_samples = {}
        changed_samples = {}
        dropped_samples = {}
        matched_refs = set()
        if headset_time is not None and band_time is not None and positions:
            allowance = max(headset_time.step_ms, band_time.step_ms) / 2
            for channel, samples in channels.items():
                if len(samples) != len(positions):
                    continue
                references = sorted(
                    (pos, target, ref.batch_id)
                    for ref in nearby if len(ref.channels.get(channel, ())) == len(ref.positions)
                    for pos, target in zip(ref.positions, ref.channels.get(channel, ()))
                )
                if not references:
                    continue
                used_position = self._used_reference.get(channel, -1)
                targets = [point for point in references if point[0] > used_position]
                target_positions = [pos for pos, _, _ in targets]
                cursor = 0
                corrected = []
                changed = 0
                for index, pos in enumerate(positions):
                    target_index = (headset_time.at(pos) - band_time.origin_ms) / band_time.step_ms
                    right = bisect_left(target_positions, target_index, lo=cursor)
                    available = range(max(cursor, right - 1), min(right + 1, len(targets)))
                    choice = min(available, key=lambda i: abs(target_positions[i] - target_index), default=None)
                    if choice is None or abs(target_positions[choice] - target_index) * band_time.step_ms > allowance:
                        continue
                    target_pos, target, ref_id = targets[choice]
                    matched_refs.add(ref_id)
                    cursor = choice + 1
                    self._used_reference[channel] = target_pos
                    value = self._clamp_to_reference(samples[index], target)
                    corrected.append(value)
                    if value != samples[index]:
                        changed += 1
                dropped = len(samples) - len(corrected)
                if changed or dropped:
                    corrected_wave[channel] = corrected
                paired_samples[channel] = len(corrected)
                changed_samples[channel] = changed
                dropped_samples[channel] = dropped

        scores = self._align_values(metrics, reference_metrics, _SCORES)
        flow = _fields(metrics.get("flow"))
        flow_score = self._align_values(flow, _fields(reference_metrics.get("flow")), ("meditation",))
        flow_fallback_reference = None
        headset_flow = flow.get("meditation")
        quality = eeg.get("quality")
        if (matched_refs and type(headset_flow) in (int, float) and headset_flow == 0
                and type(quality) in (int, float) and isfinite(quality) and quality > 0):
            # Prefer the same receive window, but the reference must have contributed paired EEG samples.
            for candidate in sorted(nearby, key=lambda ref: abs(ref.window_end_ms - batch.window_end_ms)):
                if candidate.batch_id not in matched_refs:
                    continue
                target = _fields(candidate.metrics.get("flow")).get("meditation")
                if type(target) in (int, float) and isfinite(target) and target > 0:
                    flow_score["meditation"] = self._clamp_to_reference(headset_flow, target)
                    flow_fallback_reference = candidate
                    break
        bands = _fields(eeg.get("bandPower"))
        reference_eeg = _fields(reference_metrics.get("eeg"))
        band_power = self._align_values(bands, _fields(reference_eeg.get("bandPower")), _BANDS)
        if not (corrected_wave or scores or flow_score or band_power):
            reason = "matched_no_change" if matched_refs else "no_time_matched_samples"
            self._log_decision(result, reason, reference, paired_samples=paired_samples,
                               changed_samples=changed_samples, dropped_samples=dropped_samples,
                               waited_ms=waited_ms, waveform_refs=tuple(sorted(matched_refs)))
            return result

        updates = dict(scores)
        if flow_score:
            updates["flow"] = {**flow, **flow_score}
        if corrected_wave or band_power:
            updated_eeg = dict(eeg)
            if corrected_wave:
                updated_eeg["wave"] = {**wave, **corrected_wave}
            if band_power:
                updated_eeg["bandPower"] = {**bands, **band_power}
            updates["eeg"] = updated_eeg
        self._log_decision(
            result, "flow_fallback" if flow_fallback_reference else "applied", reference,
            paired_samples=paired_samples, changed_samples=changed_samples,
            dropped_samples=dropped_samples, waited_ms=waited_ms, waveform_refs=tuple(sorted(matched_refs)),
            fields=(*scores, *("flow." + field for field in flow_score), *("bandPower." + field for field in band_power)),
            flow_fallback_reference=flow_fallback_reference,
        )
        return replace(result, algorithm_result=replace(result.algorithm_result, metrics={**metrics, **updates}))

    def _reference_covers(self, window_end_ms: int, positions: tuple[int, ...],
                          headset_time: EegSampleTime | None, band_clock: EegPacketClock) -> bool:
        next_end = window_end_ms + self._window_ms
        latest = next((ref for ref in reversed(self._references)
                       if window_end_ms <= ref.window_end_ms <= next_end and (ref.channels or ref.raw_eeg_all_ff)), None)
        if latest is None:
            return bool(self._references and self._references[-1].window_end_ms > next_end)
        band_time = band_clock.estimate()
        if latest.raw_eeg_all_ff or not positions or headset_time is None or band_time is None:
            return True
        return bool(latest.positions and band_time.at(latest.positions[-1]) + band_time.step_ms / 2 >= headset_time.at(positions[-1]))

    def _align_values(self, values: Mapping[str, object], reference: Mapping[str, object],
                      fields: tuple[str, ...]) -> dict[str, float]:
        updates = {}
        for field in fields:
            value, target = values.get(field), reference.get(field)
            # Zero is also the SDK placeholder when a metric has not been calculated.
            if (type(value) in (int, float) and type(target) in (int, float)
                    and value and target and isfinite(value) and isfinite(target)):
                corrected = self._clamp_to_reference(value, target)
                if corrected != value:
                    updates[field] = corrected
        return updates

    def _clamp_to_reference(self, value: float, target: float) -> float:
        tolerance = abs(target) * self._ratio
        return min(target + tolerance, max(target - tolerance, value))

    def _log_decision(self, result: WindowResult, reason: str, reference: _Reference | None,
                      *, paired_samples: dict[str, int] | None = None,
                      changed_samples: dict[str, int] | None = None, dropped_samples: dict[str, int] | None = None,
                      waited_ms: int = 0, waveform_refs: tuple[str, ...] = (),
                      fields: tuple[str, ...] = (), flow_fallback_reference: _Reference | None = None) -> None:
        now = time.monotonic()
        if now - self._last_log_at >= _LOG_INTERVAL_SECONDS:
            if self._decisions:
                LOG.info("Headset EEG alignment summary: windowsByReason=%s", dict(self._decisions))
            self._decisions.clear()
            self._last_log_at = now
        level = logging.INFO if not self._decisions[reason] else logging.DEBUG
        self._decisions[reason] += 1
        batch = result.batch
        LOG.log(level,
                "Headset EEG alignment decision: batchId=%s headsetSession=%s referenceBatchId=%s "
                "windowStartMs=%s windowEndMs=%s stage=algorithm_output reason=%s "
                "waveTolerancePercent=%g pairedSamples=%s changedSamples=%s droppedSamples=%s waitedMs=%s "
                "waveformReferenceBatchIds=%s fields=%s flowFallbackReferenceBatchId=%s",
                batch.batch_id, batch.connection_session_id, reference.batch_id if reference else None,
                batch.window_start_ms, batch.window_end_ms, reason, self._ratio * 100,
                paired_samples, changed_samples, dropped_samples, waited_ms, waveform_refs, fields,
                flow_fallback_reference.batch_id if flow_fallback_reference else None)
