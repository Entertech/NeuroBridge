from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from ..config import AlgorithmConfig
from ..ble.packets import DataWindow

LOG = logging.getLogger(__name__)


class AlgorithmRunner:
    """Isolates the C++ SDK behind a line-delimited JSON bridge.

    It intentionally does not call appendEEG/appendHR until the bridge command and
    the FF31/FF51 grouping has been validated with recorded device bytes.
    """
    def __init__(self, config: AlgorithmConfig) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self.error: str | None = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        if self.process is None:
            return False
        if self.process.returncode is None:
            return True
        if self.error is None:
            self.error = f"algorithm bridge exited with code {self.process.returncode}"
            LOG.error("Algorithm bridge is unavailable: %s", self.error)
        return False

    async def start(self) -> None:
        async with self._lock:
            await self._start()

    async def _start(self) -> None:
        if not self.config.enabled:
            return
        if not self.config.command:
            self.error = "algorithm.enabled requires algorithm.command"
            LOG.error("Cannot start algorithm bridge: %s", self.error)
            return
        try:
            self.process = await asyncio.create_subprocess_exec(*self.config.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
            LOG.info("Algorithm process started: pid=%s evaluationTimeoutSeconds=%s stopTimeoutSeconds=%s", self.process.pid, self.config.evaluation_timeout_seconds, self.config.stop_timeout_seconds)
        except OSError as exc:
            self.error = str(exc) or type(exc).__name__
            LOG.exception("Cannot start algorithm bridge: %s", self.error)

    async def initialize(self) -> None:
        """Create a clean SDK process for each new device connection/session."""
        async with self._lock:
            await self._stop()
            self.error = None
            await self._start()

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    async def _stop(self) -> None:
        process, self.process = self.process, None
        if process is None or process.returncode is not None:
            return
        started = time.monotonic()
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), self.config.stop_timeout_seconds)
        except ProcessLookupError:
            pass
        except TimeoutError:
            LOG.warning("Algorithm stop timed out: pid=%s timeoutSeconds=%s; forcing termination", process.pid, self.config.stop_timeout_seconds)
            try:
                process.kill()
            except ProcessLookupError:
                pass
            # Even an unresponsive OS process cannot block the BLE retry loop.
            try:
                await asyncio.wait_for(process.wait(), self.config.stop_timeout_seconds)
            except TimeoutError:
                LOG.error("Algorithm kill wait timed out: pid=%s; process isolated", process.pid)
        LOG.info("Algorithm process retired: pid=%s elapsedMs=%s returnCode=%s", process.pid, round((time.monotonic() - started) * 1000), process.returncode)

    async def evaluate(self, window: DataWindow) -> tuple[dict | None, list[str]]:
        async with self._lock:
            return await self._evaluate(window)

    async def _evaluate(self, window: DataWindow) -> tuple[dict | None, list[str]]:
        if not window.eeg and not window.hr:
            return None, []
        if not self.available or not self.process or not self.process.stdin or not self.process.stdout:
            return None, ["ALGORITHM_NOT_READY"]
        started = time.monotonic()
        try:
            request = {"timestampMs": window.end_ms, "eegRawBase64": base64.b64encode(b"".join(x.value for x in window.eeg)).decode(), "hrRawBase64": base64.b64encode(b"".join(x.value for x in window.hr)).decode()}
            async with asyncio.timeout(self.config.evaluation_timeout_seconds):
                self.process.stdin.write((json.dumps(request) + "\n").encode())
                await self.process.stdin.drain()
                response = await self.process.stdout.readline()
            if not response:
                raise RuntimeError("algorithm bridge closed stdout without a response")
            result = json.loads(response)
            if not isinstance(result, dict):
                raise ValueError("algorithm response must be an object")
            bridge_error = result.get("bridgeError") or result.get("pocError")
            if bridge_error:
                raise ValueError(f"algorithm bridge: {bridge_error}")
            if not isinstance(result.get("algorithm"), dict):
                return None, ["ALGORITHM_OUTPUT_INVALID"]
            return result["algorithm"], []
        except asyncio.CancelledError:
            self.error = "algorithm exchange cancelled; awaiting next device session"
            LOG.warning("Algorithm exchange cancelled: windowEndMs=%s; retiring stream to prevent stale responses", window.end_ms)
            await self._stop()
            raise
        except (asyncio.TimeoutError, json.JSONDecodeError, OSError, UnicodeError, ValueError, RuntimeError) as exc:
            self.error = str(exc) or type(exc).__name__
            LOG.warning("Algorithm bridge evaluation failed: windowStartMs=%s windowEndMs=%s elapsedMs=%s errorType=%s; retiring stream until next device session", window.start_ms, window.end_ms, round((time.monotonic() - started) * 1000), type(exc).__name__)
            await self._stop()
            return None, ["ALGORITHM_ERROR"]
