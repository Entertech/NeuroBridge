"""Algorithm input mapping and bounded result aggregation."""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Awaitable, Callable, Iterable

from ..domain.algorithm import AlgorithmInput, AlgorithmResult
from ..domain.raw import DeviceFrame
from ..domain.result import WindowResult
from ..domain.signal import ParsedSignalBatch
from ..ports.algorithm import AlgorithmEngine


class HrRateNormalizer:
    """Convert fast, repeated HR readings into a stable 5 Hz sample stream.

    HR hardware reports the current BPM on every frame (tens of times per
    second), mostly repeating the same value. The SDK expects 5 Hz, so each
    200 ms slot emits the latest value observed in that slot; missing slots are
    zero-order held only while the stream is continuous. Values may repeat when
    the measured BPM really is constant; they are not synthetic duplicates.
    """

    def __init__(self, rate_hz: int = 5) -> None:
        if rate_hz <= 0:
            raise ValueError("HR rate must be positive")
        self.rate_hz = rate_hz
        self.slot_ms = max(1, round(1000 / rate_hz))
        # Device batches can straddle a slot boundary (e.g. ~220 ms serial
        # chunks), so a one-slot hole is normal jitter and is zero-order held.
        # Only a gap of more than one second means the HR stream really stopped.
        self.max_hold_slots = max(2, rate_hz)
        self._latest_by_slot: dict[int, int] = {}
        self._last_emitted_slot: int | None = None
        self._last_value: int | None = None

    def reset(self) -> None:
        self._latest_by_slot.clear()
        self._last_emitted_slot = None
        self._last_value = None

    def push(self, samples: Iterable[tuple[int, int]], window_end_ms: int) -> bytes:
        readings = list(samples)
        if not readings:
            return b""
        earliest_slot = min(timestamp_ms // self.slot_ms for timestamp_ms, _ in readings)
        if self._last_emitted_slot is not None and earliest_slot - self._last_emitted_slot > self.max_hold_slots:
            # A real data gap must not be padded with stale HR values.
            self.reset()
        if self._last_emitted_slot is None:
            self._last_emitted_slot = earliest_slot - 1
        for timestamp_ms, value in readings:
            slot = timestamp_ms // self.slot_ms
            if slot > self._last_emitted_slot:
                self._latest_by_slot[slot] = value
        closed_slot = window_end_ms // self.slot_ms - 1
        if closed_slot <= self._last_emitted_slot:
            return b""
        emitted: list[int] = []
        for slot in range(self._last_emitted_slot + 1, closed_slot + 1):
            value = self._latest_by_slot.pop(slot, None)
            if value is None:
                if self._last_value is None:
                    continue
                value = self._last_value
            else:
                self._last_value = value
            emitted.append(value)
            self._last_emitted_slot = slot
        if self._last_emitted_slot < closed_slot:
            # No previous value existed for the skipped leading slots.
            self._last_emitted_slot = closed_slot
        return bytes(emitted)


class AlgorithmInputMapper:
    MAPPING_VERSION = "2"
    HR_MAX_PER_SECOND = 5

    def __init__(self, *, pace_headset_hr: bool = False, hr_max_per_second: int = HR_MAX_PER_SECOND) -> None:
        # pace_headset_hr is ignored. Both devices share the same heart-rate cap.
        del pace_headset_hr
        if hr_max_per_second <= 0:
            raise ValueError("hr_max_per_second must be positive")
        self.hr_max_per_second = hr_max_per_second
        self._hr_normalizer = HrRateNormalizer(hr_max_per_second)
        self._hr_session_id: str | None = None

    def map(self, batch: ParsedSignalBatch, frames: Iterable[DeviceFrame] = ()) -> AlgorithmInput:
        frame_by_id = {frame.frame_id: frame for frame in frames}
        payload: dict[str, object] = {"windowStartMs": batch.window_start_ms, "windowEndMs": batch.window_end_ms}
        mapping_version = self.MAPPING_VERSION
        hr_samples: list[tuple[int, int]] = []
        if batch.device_protocol == "headset_rev181":
            ordered = [frame_by_id[ref] for ref in batch.frame_refs if ref in frame_by_id]
            hr_samples = [(frame.received_at_ms, frame.raw_bytes[24]) for frame in ordered]
            payload["eegRawBase64"] = base64.b64encode(b"".join(frame.raw_bytes[4:24] for frame in ordered)).decode("ascii")
            payload["eegPacketCount"] = len(ordered)
        else:
            for signal_type in ("eeg",):
                chunks = [signal.samples for signal in batch.signals if signal.signal_type == signal_type and isinstance(signal.samples, bytes)]
                payload[f"{signal_type}RawBase64"] = base64.b64encode(b"".join(chunks)).decode("ascii")
                payload[f"{signal_type}PacketCount"] = len(chunks)
            for signal in batch.signals:
                if signal.signal_type == "hr" and isinstance(signal.samples, bytes):
                    hr_samples.extend((signal.received_at_ms, value) for value in signal.samples)
        if batch.connection_session_id != self._hr_session_id:
            self._hr_normalizer.reset()
            self._hr_session_id = batch.connection_session_id
        kept = self._hr_normalizer.push(hr_samples, batch.window_end_ms)
        payload["hrRawBase64"] = base64.b64encode(kept).decode("ascii")
        payload["hrPacketCount"] = len(kept)
        return AlgorithmInput(batch.batch_id, payload, mapping_version, batch.device_protocol)


class WindowResultAggregator:
    def __init__(self, engine: AlgorithmEngine, timeout_ms: int = 2000, max_slots: int = 8) -> None:
        if timeout_ms <= 0 or max_slots <= 0:
            raise ValueError("Aggregator limits must be positive")
        self.engine = engine
        self.timeout_ms = timeout_ms
        self.max_slots = max_slots
        self._open: dict[str, asyncio.Task[AlgorithmResult]] = {}
        self._background: set[asyncio.Task[None]] = set()
        self._closed = False
        self.late_result_count = 0

    async def evaluate(
        self,
        batch: ParsedSignalBatch,
        value: AlgorithmInput,
        *,
        persistence_guaranteed: bool,
        on_late_result: Callable[[AlgorithmResult], Awaitable[None]] | None = None,
    ) -> WindowResult:
        if self._closed:
            raise RuntimeError("WindowResultAggregator is closed")
        if batch.batch_id != value.batch_id:
            raise ValueError("Algorithm input does not belong to the batch")
        if len(self._open) >= self.max_slots:
            result = self._invalid(batch.batch_id, "ALGORITHM_BACKLOG")
            return WindowResult(batch, result, "live", result.completed_at_ms, persistence_guaranteed)
        task = asyncio.create_task(self.engine.evaluate(value))
        self._open[batch.batch_id] = task
        try:
            # Python 3.11 wait_for can consume an external cancellation if the
            # SDK result becomes ready while the connection is being closed.
            async with asyncio.timeout(self.timeout_ms / 1000):
                result = await asyncio.shield(task)
            self._open.pop(batch.batch_id, None)
        except TimeoutError:
            result = self._invalid(batch.batch_id, "ALGORITHM_TIMEOUT")
            background = asyncio.create_task(
                self._collect_late_result(batch.batch_id, task, on_late_result)
            )
            self._background.add(background)
            background.add_done_callback(self._background.discard)
            background.add_done_callback(self._observe_background_failure)
        except Exception:
            self._open.pop(batch.batch_id, None)
            result = self._invalid(batch.batch_id, "ALGORITHM_ERROR")
        return WindowResult(batch, result, "live", result.completed_at_ms, persistence_guaranteed)

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(self._open.values())
        self._open.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        background = tuple(self._background)
        if background:
            await asyncio.gather(*background, return_exceptions=True)

    async def _collect_late_result(
        self,
        batch_id: str,
        task: asyncio.Task[AlgorithmResult],
        on_late_result: Callable[[AlgorithmResult], Awaitable[None]] | None,
    ) -> None:
        try:
            result = await task
        except asyncio.CancelledError:
            return
        except Exception:
            return
        finally:
            self._open.pop(batch_id, None)
        self.late_result_count += 1
        if on_late_result is not None:
            await on_late_result(result)

    @staticmethod
    def _observe_background_failure(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        # Reading the exception marks it as observed and prevents an
        # unhandled-task warning during application shutdown.
        task.exception()

    @staticmethod
    def _invalid(batch_id: str, reason: str) -> AlgorithmResult:
        timestamp = int(time.time() * 1000)
        return AlgorithmResult(batch_id, None, timestamp, timestamp, {}, False, (reason,))
