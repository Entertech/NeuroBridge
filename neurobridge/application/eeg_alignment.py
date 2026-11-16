"""Calibrate Windows headset EEG outputs against completed headband results."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import isfinite
from statistics import median
import logging
import time

from ..domain.result import WindowResult
from ..domain.signal import ParsedSignalBatch


LOG = logging.getLogger(__name__)
_CHANNELS = ("left", "right")
_SCORES = ("relaxation", "attention", "pleasure")
_BANDS = ("alpha", "beta", "theta", "delta", "gamma", "lowBeta", "highBeta")
_WAVE_LIMIT = 2000  # The native SDK dual-channel waveform limit.
_LOG_INTERVAL_SECONDS = 10


@dataclass(frozen=True, slots=True)
class _Reference:
    batch_id: str
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
    def __init__(self, *, percent: int) -> None:
        self._ratio = percent / 100
        self._references: dict[tuple[int, int], _Reference] = {}
        self._decisions: Counter[str] = Counter()
        self._last_log_at = time.monotonic()

    def align_result(self, result: WindowResult) -> WindowResult:
        if not self._ratio:
            return result
        batch = result.batch
        key = (batch.window_start_ms, batch.window_end_ms)
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
            # Retain the two latest windows, including unusable results so they
            # cannot accidentally reuse a previous valid window.
            self._references[key] = _Reference(batch.batch_id, channels, metrics, raw_eeg_all_ff)
            if len(self._references) > 2:
                del self._references[min(self._references)]
            return result

        duration_ms = batch.window_end_ms - batch.window_start_ms
        reference = self._references.get(key)
        if reference is None:
            reference = self._references.get((batch.window_start_ms - duration_ms, batch.window_start_ms))

        reason = None
        if raw_eeg_all_ff:
            reason = "headset_raw_eeg_all_ff"
        elif not result.valid:
            reason = "headset_invalid_output"
        elif not channels:
            reason = "headset_no_usable_waveform"
        elif reference is None:
            reason = "no_reference_window"
        elif reference.raw_eeg_all_ff:
            reason = "headband_raw_eeg_all_ff"
        elif not reference.channels:
            reason = "headband_no_usable_waveform"
        if reason is not None:
            self._log_decision(result, reason, reference)
            return result

        corrected_wave = {}
        paired_samples = {}
        changed_samples = {}
        for channel, samples in channels.items():
            targets = reference.channels.get(channel)
            if targets is None:
                continue
            paired_samples[channel] = min(len(samples), len(targets))
            corrected = list(samples)
            changed = 0
            for index, (sample, target) in enumerate(zip(samples, targets)):
                tolerance = abs(target) * self._ratio
                lower = max(-_WAVE_LIMIT, target - tolerance)
                upper = min(_WAVE_LIMIT, target + tolerance)
                if not lower <= sample <= upper:
                    corrected[index] = min(upper, max(lower, sample))
                    changed += 1
            if changed:
                corrected_wave[channel] = corrected
            changed_samples[channel] = changed

        scores = self._align_values(metrics, reference.metrics, _SCORES)
        flow = _fields(metrics.get("flow"))
        flow_score = self._align_values(flow, _fields(reference.metrics.get("flow")), ("meditation",))
        bands = _fields(eeg.get("bandPower"))
        reference_eeg = _fields(reference.metrics.get("eeg"))
        band_power = self._align_values(bands, _fields(reference_eeg.get("bandPower")), _BANDS)
        if not (corrected_wave or scores or flow_score or band_power):
            self._log_decision(result, "matched_no_change", reference, paired_samples=paired_samples,
                               changed_samples=changed_samples)
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
            result, "applied", reference, paired_samples=paired_samples, changed_samples=changed_samples,
            fields=(*scores, *("flow." + field for field in flow_score), *("bandPower." + field for field in band_power)),
        )
        return replace(result, algorithm_result=replace(result.algorithm_result, metrics={**metrics, **updates}))

    def _align_values(self, values: Mapping[str, object], reference: Mapping[str, object],
                      fields: tuple[str, ...]) -> dict[str, float]:
        updates = {}
        for field in fields:
            value, target = values.get(field), reference.get(field)
            # Zero is also the SDK placeholder when a metric has not been calculated.
            if (type(value) in (int, float) and type(target) in (int, float)
                    and value and target and isfinite(value) and isfinite(target) and value != target):
                updates[field] = value + (target - value) * self._ratio
        return updates

    def _log_decision(self, result: WindowResult, reason: str, reference: _Reference | None,
                      *, paired_samples: dict[str, int] | None = None,
                      changed_samples: dict[str, int] | None = None, fields: tuple[str, ...] = ()) -> None:
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
                "waveTolerancePercent=%g pairedSamples=%s changedSamples=%s fields=%s",
                batch.batch_id, batch.connection_session_id, reference.batch_id if reference else None,
                batch.window_start_ms, batch.window_end_ms, reason, self._ratio * 100,
                paired_samples, changed_samples, fields)
