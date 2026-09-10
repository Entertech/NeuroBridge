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
            payload["eegPacketCount"] = len(ordered)
            payload["hrPacketCount"] = len(ordered)
        else:
            for signal_type in ("eeg", "hr"):
                chunks = [signal.samples for signal in batch.signals if signal.signal_type == signal_type and isinstance(signal.samples, bytes)]
                payload[f"{signal_type}RawBase64"] = base64.b64encode(b"".join(chunks)).decode("ascii")
                payload[f"{signal_type}PacketCount"] = len(chunks)
        return AlgorithmInput(batch.batch_id, payload, self.MAPPING_VERSION)


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
            result = await asyncio.wait_for(asyncio.shield(task), self.timeout_ms / 1000)
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
