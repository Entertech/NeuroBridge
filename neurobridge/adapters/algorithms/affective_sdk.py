"""Domain-facing adapter for the existing isolated C++ SDK bridge."""

from __future__ import annotations

import base64
import time

from ...algorithm.runner import AlgorithmRunner
from ...ble.packets import DataWindow, RawPacket
from ...config import AlgorithmConfig
from ...domain.algorithm import AlgorithmInput, AlgorithmResult, AlgorithmSession, AlgorithmState


class AffectiveSdkAlgorithmEngine:
    def __init__(self, config: AlgorithmConfig, algorithm_version: str | None = None) -> None:
        self._runner = AlgorithmRunner(config)
        self.algorithm_version = algorithm_version

    async def initialize(self, session: AlgorithmSession) -> AlgorithmState:
        await self._runner.initialize()
        if self._runner.available:
            return AlgorithmState.READY
        return AlgorithmState.ERROR if self._runner.error else AlgorithmState.UNAVAILABLE

    async def evaluate(self, value: AlgorithmInput) -> AlgorithmResult:
        started = int(time.time() * 1000)
        try:
            eeg = base64.b64decode(str(value.payload.get("eegRawBase64", "")), validate=True)
            hr = base64.b64decode(str(value.payload.get("hrRawBase64", "")), validate=True)
        except ValueError:
            return AlgorithmResult(value.batch_id, self.algorithm_version, started, int(time.time() * 1000), {}, False, ("ALGORITHM_INPUT_INVALID",))
        start_ms = int(value.payload.get("windowStartMs", started))
        end_ms = int(value.payload.get("windowEndMs", started))
        window = DataWindow(start_ms, end_ms)
        if eeg:
            window.eeg.append(RawPacket("ff31", end_ms, eeg))
        if hr:
            window.hr.append(RawPacket("ff51", end_ms, hr))
        metrics, reasons = await self._runner.evaluate(window)
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
