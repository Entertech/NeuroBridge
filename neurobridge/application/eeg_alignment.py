"""Show EEG algorithm output according to headset and headband wear.

When both devices are worn, the headset shows the latest headband EEG as soon
as it exists. No window time is required. Heart metrics stay with each device.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import logging
import time

from ..domain.result import WindowResult
from ..domain.signal import ParsedSignalBatch


LOG = logging.getLogger(__name__)
_EEG_DISPLAY_FIELDS = ("eeg", "attention", "relaxation", "pleasure", "flow", "sleep")
_VITAL_FIELDS = ("hr", "pressure", "coherence", "arousal")
_LOG_INTERVAL_SECONDS = 10
# Headset frames store two 3-byte samples per channel. Headband BLE packets
# keep a 2-byte sequence number before the same 3-byte samples.
_HEADSET_EEG_BYTES = 18
_HEADBAND_SEQUENCE_BYTES = 2
_SAMPLE_BYTES = 3


def _sample_bytes(batch: ParsedSignalBatch) -> bytes:
    offset = _HEADBAND_SEQUENCE_BYTES if batch.device_protocol == "headband_ble" else 0
    chunks = []
    for signal in batch.signals:
        if signal.signal_type != "eeg" or not signal.valid or not isinstance(signal.samples, bytes):
            continue
        samples = signal.samples[offset:]
        if batch.device_protocol == "headset_rev181" and len(signal.samples) != _HEADSET_EEG_BYTES:
            continue
        if samples:
            chunks.append(samples)
    return b"".join(chunks)


def display_eeg(headset_worn: bool | None, headband_connected: bool, headband_worn: bool | None,
                *, source: str) -> bool:
    """Apply the four wear combinations to one device's EEG display.

    Heart rate, HRV, and pressure are intentionally absent: each device keeps
    its own values. ``None`` means that device has not reported raw EEG yet.
    """

    if source == "headset":
        if headset_worn is False or (headband_connected and headband_worn is False and headset_worn is not True):
            return False
        return True
    if headband_worn is False:
        return False
    return True


def raw_eeg_worn(batch: ParsedSignalBatch) -> bool | None:
    """Return wear from raw EEG, or None when this window has no EEG samples.

    A device is unworn when every sample is 0xFFFFFF, or either channel is
    entirely 0x000000. Heart-rate bytes are not part of this decision.
    """

    samples = _sample_bytes(batch)
    if not samples:
        return None
    usable = len(samples) - len(samples) % (2 * _SAMPLE_BYTES)
    if usable < 2 * _SAMPLE_BYTES:
        return None
    left = [samples[index:index + _SAMPLE_BYTES] for index in range(0, usable, 2 * _SAMPLE_BYTES)]
    right = [samples[index:index + _SAMPLE_BYTES] for index in range(_SAMPLE_BYTES, usable, 2 * _SAMPLE_BYTES)]
    if not left or len(left) != len(right):
        return None
    if all(sample == b"\x00\x00\x00" for sample in left) or all(sample == b"\x00\x00\x00" for sample in right):
        return False
    return any(sample != b"\xff\xff\xff" for sample in (*left, *right))


class EegAlignmentCache:
    """Keep the latest wear of each connected device and apply the display rule.

    Heart rate, HRV, and pressure stay on the device that measured them. Only
    the EEG waveform and the other EEG algorithm fields move between devices.
    """

    def __init__(self, *, percent: int = 0, window_ms: int = 600, wait_ms: int = 0,
                 reference_batches: int = 16, clock_packets: int = 4096,
                 clock_ms: Callable[[], int] | None = None) -> None:
        del percent, window_ms, wait_ms, reference_batches, clock_packets, clock_ms
        self._sessions: dict[str, str | None] = {}
        self._worn: dict[str, bool] = {}
        self.display_eeg: dict[str, bool] = {}
        self._latest_eeg: dict[str, object] | None = None
        self._latest_eeg_batch_id: str | None = None
        self._decisions: dict[str, int] = {}
        self._last_log_at = time.monotonic()

    def set_session(self, protocol: str, session: str | None) -> None:
        if self._sessions.get(protocol) == session:
            return
        self._sessions[protocol] = session
        self._worn.pop(protocol, None)
        self.display_eeg.pop(protocol, None)
        if protocol == "headband_ble" or session is None:
            self._latest_eeg = None
            self._latest_eeg_batch_id = None

    async def align_result(self, result: WindowResult) -> WindowResult:
        protocol = result.batch.device_protocol
        if protocol not in self._sessions:
            return result
        worn = raw_eeg_worn(result.batch)
        if worn is not None:
            self._worn[protocol] = worn
        self._refresh_display(protocol)
        if protocol == "headband_ble":
            return self._remember_headband(result)
        if protocol != "headset_rev181":
            return result
        return self._present_headset(result)

    def _refresh_display(self, protocol: str) -> None:
        headset_worn = self._worn.get("headset_rev181")
        headband_connected = self._sessions.get("headband_ble") is not None
        headband_worn = self._worn.get("headband_ble")
        source = "headset" if protocol == "headset_rev181" else "headband"
        self.display_eeg[protocol] = display_eeg(
            headset_worn, headband_connected, headband_worn, source=source,
        )

    def _remember_headband(self, result: WindowResult) -> WindowResult:
        if self._worn.get("headband_ble") is False:
            self._latest_eeg = None
            self._latest_eeg_batch_id = None
            hidden = self._without_eeg(result)
            self._log_decision(result, "headband_unworn_hidden")
            return hidden
        metrics = result.algorithm_result.metrics
        eeg = {field: metrics[field] for field in _EEG_DISPLAY_FIELDS if field in metrics}
        if eeg:
            self._latest_eeg = eeg
            self._latest_eeg_batch_id = result.batch.batch_id
        self._log_decision(result, "headband_shown")
        return result

    def _present_headset(self, result: WindowResult) -> WindowResult:
        headset_worn = self._worn.get("headset_rev181")
        headband_connected = self._sessions.get("headband_ble") is not None
        headband_worn = self._worn.get("headband_ble") is True
        headband_unworn = self._worn.get("headband_ble") is False
        if headset_worn is False or (headband_connected and headband_unworn and headset_worn is not True):
            shown = self._without_eeg(result)
            self._log_decision(shown, "headset_unworn_hidden")
            return shown
        if headset_worn and headband_connected and headband_worn and self._latest_eeg:
            shown = self._use_latest_headband(result)
            self._log_decision(shown, "headset_uses_latest_headband", self._latest_eeg_batch_id)
            return shown
        self._log_decision(result, "headset_shown")
        return result

    def _use_latest_headband(self, result: WindowResult) -> WindowResult:
        """Replace headset EEG with the newest headband EEG, whenever it arrived."""
        reference = self._latest_eeg or {}
        metrics = dict(result.algorithm_result.metrics)
        for field in _EEG_DISPLAY_FIELDS:
            if field in reference:
                metrics[field] = reference[field]
        return replace(result, algorithm_result=replace(result.algorithm_result, metrics=metrics))

    @staticmethod
    def _without_eeg(result: WindowResult) -> WindowResult:
        metrics = {
            field: value for field, value in result.algorithm_result.metrics.items() if field in _VITAL_FIELDS
        }
        return replace(result, algorithm_result=replace(result.algorithm_result, metrics=metrics))

    def _log_decision(self, result: WindowResult, reason: str, reference_batch_id: str | None = None) -> None:
        now = time.monotonic()
        if now - self._last_log_at >= _LOG_INTERVAL_SECONDS:
            if self._decisions:
                LOG.info("EEG wear display summary: windowsByReason=%s", dict(self._decisions))
            self._decisions.clear()
            self._last_log_at = now
        level = logging.INFO if not self._decisions.get(reason) else logging.DEBUG
        self._decisions[reason] = self._decisions.get(reason, 0) + 1
        batch = result.batch
        LOG.log(
            level,
            "EEG wear display: batchId=%s deviceProtocol=%s headsetWorn=%s headbandWorn=%s "
            "reason=%s referenceBatchId=%s",
            batch.batch_id, batch.device_protocol, self._worn.get("headset_rev181"),
            self._worn.get("headband_ble"), reason, reference_batch_id,
        )
