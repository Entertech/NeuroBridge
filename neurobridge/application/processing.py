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


class AlgorithmInputMapper:
    MAPPING_VERSION = "1"

    def map(self, batch: ParsedSignalBatch, frames: Iterable[DeviceFrame] = ()) -> AlgorithmInput:
        frame_by_id = {frame.frame_id: frame for frame in frames}
        payload: dict[str, object] = {"windowStartMs": batch.window_start_ms, "windowEndMs": batch.window_end_ms}
        if batch.device_protocol == "headset_rev181":
            ordered = [frame_by_id[ref] for ref in batch.frame_refs if ref in frame_by_id]
            payload["eegRawBase64"] = base64.b64encode(b"".join(frame.raw_bytes[4:24] for frame in ordered)).decode("ascii")
            payload["hrRawBase64"] = base64.b64encode(b"".join(frame.raw_bytes[24:25] for frame in ordered)).decode("ascii")
        else:
            for signal_type in ("eeg", "hr"):
                chunks = [signal.samples for signal in batch.signals if signal.signal_type == signal_type and isinstance(signal.samples, bytes)]
                payload[f"{signal_type}RawBase64"] = base64.b64encode(b"".join(chunks)).decode("ascii")
        return AlgorithmInput(batch.batch_id, payload, self.MAPPING_VERSION)


class WindowResultAggregator:
    def __init__(self, engine: AlgorithmEngine, timeout_ms: int = 2000, max_slots: int = 8) -> None:
        if timeout_ms <= 0 or max_slots <= 0:
            raise ValueError("Aggregator limits must be positive")
        self.engine = engine
        self.timeout_ms = timeout_ms
        self.max_slots = max_slots
        self._open: dict[str, asyncio.Task[AlgorithmResult]] = {}
        self.late_result_count = 0

    async def evaluate(
        self,
        batch: ParsedSignalBatch,
        value: AlgorithmInput,
        *,
        persistence_guaranteed: bool,
        on_late_result: Callable[[AlgorithmResult], Awaitable[None]] | None = None,
    ) -> WindowResult:
        if batch.batch_id != value.batch_id:
            raise ValueError("Algorithm input does not belong to the batch")
        if len(self._open) >= self.max_slots:
            result = self._invalid(batch.batch_id, "ALGORITHM_BACKLOG")
            return WindowResult(batch, result, "live", result.completed_at_ms, persistence_guaranteed)
        task = asyncio.create_task(self.engine.evaluate(value))
        self._open[batch.batch_id] = task
        try:
            result = await asyncio.wait_for(asyncio.shield(task), self.timeout_ms / 1000)
            self._open.pop(batch.batch_id, None)
        except TimeoutError:
            self._open.pop(batch.batch_id, None)
            result = self._invalid(batch.batch_id, "ALGORITHM_TIMEOUT")

            def completed(late_task: asyncio.Task[AlgorithmResult]) -> None:
                self.late_result_count += 1
                if on_late_result is not None and not late_task.cancelled() and late_task.exception() is None:
                    asyncio.create_task(on_late_result(late_task.result()))

            task.add_done_callback(completed)
        except Exception:
            self._open.pop(batch.batch_id, None)
            result = self._invalid(batch.batch_id, "ALGORITHM_ERROR")
        return WindowResult(batch, result, "live", result.completed_at_ms, persistence_guaranteed)

    async def close(self) -> None:
        tasks = tuple(self._open.values())
        self._open.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _invalid(batch_id: str, reason: str) -> AlgorithmResult:
        timestamp = int(time.time() * 1000)
        return AlgorithmResult(batch_id, None, timestamp, timestamp, {}, False, (reason,))
