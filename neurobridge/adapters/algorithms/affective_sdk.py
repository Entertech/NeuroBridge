"""Domain-facing adapter for the existing isolated C++ SDK bridge."""

from __future__ import annotations

import base64
import asyncio
import time

from ...algorithm.runner import AlgorithmRunner
from ...config import AlgorithmConfig
from ...domain.algorithm import AlgorithmInput, AlgorithmResult, AlgorithmSession, AlgorithmState


class AffectiveSdkAlgorithmEngine:
    def __init__(self, config: AlgorithmConfig, algorithm_version: str | None = None) -> None:
        self._runner = AlgorithmRunner(config)
        self.algorithm_version = algorithm_version
        self._evaluation_lock = asyncio.Lock()

    async def initialize(self, session: AlgorithmSession) -> AlgorithmState:
        await self._runner.initialize()
        if self._runner.available:
            return AlgorithmState.READY
        return AlgorithmState.ERROR if self._runner.error else AlgorithmState.UNAVAILABLE

    async def evaluate(self, value: AlgorithmInput) -> AlgorithmResult:
        # A timed-out aggregation slot may still be collecting a late result.
        # The line-oriented SDK bridge permits only one outstanding request.
        async with self._evaluation_lock:
            return await self._evaluate(value)

    async def _evaluate(self, value: AlgorithmInput) -> AlgorithmResult:
        started = int(time.time() * 1000)
        try:
            eeg = base64.b64decode(str(value.payload.get("eegRawBase64", "")), validate=True)
            hr = base64.b64decode(str(value.payload.get("hrRawBase64", "")), validate=True)
        except ValueError:
            return AlgorithmResult(value.batch_id, self.algorithm_version, started, int(time.time() * 1000), {}, False, ("ALGORITHM_INPUT_INVALID",))
        start_ms = int(value.payload.get("windowStartMs", started))
        end_ms = int(value.payload.get("windowEndMs", started))
        metrics, reasons = await self._runner.evaluate_raw(
            eeg,
            hr,
            start_ms=start_ms,
            end_ms=end_ms,
            eeg_packet_count=int(value.payload.get("eegPacketCount", 0)),
            hr_packet_count=int(value.payload.get("hrPacketCount", 0)),
        )
        return AlgorithmResult(
            value.batch_id,
            self.algorithm_version,
            started,
            int(time.time() * 1000),
            metrics or {},
            not reasons,
            tuple(reasons),
        )

    async def close(self) -> None:
        await self._runner.stop()

    @property
    def available(self) -> bool:
        return self._runner.available

    async def stop(self) -> None:
        await self.close()
