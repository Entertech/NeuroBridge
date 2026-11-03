"""Domain-facing adapter for the existing isolated C++ SDK bridge."""

from __future__ import annotations

import base64
import asyncio
import time

from ...algorithm.runner import AlgorithmRunner
from ...config import AlgorithmConfig
from ...hrv_diagnostics import HrvTrace
from ...domain.algorithm import AlgorithmInput, AlgorithmResult, AlgorithmSession, AlgorithmState


class AffectiveSdkAlgorithmEngine:
    def __init__(self, config: AlgorithmConfig, algorithm_version: str | None = None) -> None:
        self._runner = AlgorithmRunner(config)
        self.algorithm_version = algorithm_version
        self._evaluation_lock = asyncio.Lock()
        self.hrv_trace = HrvTrace()
        self._diagnostic_session = {}

    async def initialize(self, session: AlgorithmSession) -> AlgorithmState:
        self._diagnostic_session = {"connectionSessionId": session.connection_session_id, "recordingSessionId": session.recording_session_id}
        await self._runner.initialize()
        if self._runner.available:
            return AlgorithmState.READY
        return AlgorithmState.ERROR if self._runner.error else AlgorithmState.UNAVAILABLE

    def configure_diagnostics(self, interval_seconds):
        self.hrv_trace.interval_seconds = interval_seconds
        self._runner.hrv_trace.interval_seconds = interval_seconds

    async def adopt_prepared(self, prepared: "AffectiveSdkAlgorithmEngine") -> None:
        """Transfer an unused, warmed SDK process into the validated session.

        The preparation never evaluates data. Its owner retains our discarded
        runner and can close it normally without terminating the adopted one.
        """
        async with self._evaluation_lock:
            await self.close()
            self._runner, prepared._runner = prepared._runner, self._runner

    async def evaluate(self, value: AlgorithmInput) -> AlgorithmResult:
        # A timed-out aggregation slot may still be collecting a late result.
        # The line-oriented SDK bridge permits only one outstanding request.
        async with self._evaluation_lock:
            result = await self._evaluate(value)
            self.hrv_trace.observe("engine_result", result.metrics, batchId=value.batch_id,
                windowStartMs=value.payload.get("windowStartMs"), windowEndMs=value.payload.get("windowEndMs"),
                expected_hr=bool(value.payload.get("hrRawBase64")), valid=result.valid, mode="live",
                hrInputPackets=value.payload.get("hrPacketCount"),
                reason=result.invalid_reasons[0] if result.invalid_reasons else None, **self._diagnostic_session)
            return result

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
            diagnostic_context={"batchId": value.batch_id, **self._diagnostic_session},
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
