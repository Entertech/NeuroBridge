from __future__ import annotations

import asyncio
import base64
import json
import logging
import time

from ..config import AlgorithmConfig

LOG = logging.getLogger(__name__)


def _safe_log_text(value: object, limit: int = 512) -> str:
    return "".join(character if character.isprintable() else " " for character in str(value))[:limit]


class AlgorithmRunner:
    """Isolates the C++ SDK behind a line-delimited JSON bridge.

    It intentionally does not call appendEEG/appendHR until the bridge command and
    the FF31/FF51 grouping has been validated with recorded device bytes.
    """
    def __init__(self, config: AlgorithmConfig) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self.error: str | None = None

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
        if not self.config.enabled:
            LOG.info("Algorithm bridge disabled by configuration")
            return
        if not self.config.command:
            self.error = "algorithm.enabled requires algorithm.command"
            LOG.error("Cannot start algorithm bridge: %s", self.error)
            return
        try:
            self.process = await asyncio.create_subprocess_exec(*self.config.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
            LOG.info("Algorithm bridge started: command=%s pid=%s", self.config.command[0], self.process.pid)
        except OSError as exc:
            self.error = str(exc)
            LOG.exception("Cannot start algorithm bridge: %s", _safe_log_text(self.error))

    async def initialize(self) -> None:
        """Create a clean SDK process for each new device connection/session."""
        LOG.info("Initializing algorithm bridge for new capture session")
        await self.stop()
        self.process = None
        self.error = None
        await self.start()

    async def stop(self) -> None:
        process, self.process = self.process, None
        if process and process.returncode is None:
            LOG.info("Stopping algorithm bridge: pid=%s", process.pid)
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), self.config.request_timeout_ms / 1000)
            except TimeoutError:
                LOG.error("Algorithm bridge ignored terminate; killing pid=%s", process.pid)
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await asyncio.wait_for(process.wait(), self.config.request_timeout_ms / 1000)
            LOG.info("Algorithm bridge stopped: returncode=%s", process.returncode)

    async def _discard_exchange(self, process: asyncio.subprocess.Process) -> None:
        # There is no request ID in this bridge protocol. Once an exchange is
        # interrupted, a late line cannot safely be assigned to another window.
        # Never close a replacement installed by a concurrent session reset.
        if self.process is not process:
            return
        try:
            await self.stop()
        except (TimeoutError, OSError) as exc:
            LOG.error("Failed to reap discarded algorithm bridge: %s", _safe_log_text(exc))

    async def evaluate(self, window: object) -> tuple[dict | None, list[str]]:
        eeg_packets = getattr(window, "eeg")
        hr_packets = getattr(window, "hr")
        return await self.evaluate_raw(
            b"".join(item.value for item in eeg_packets),
            b"".join(item.value for item in hr_packets),
            start_ms=getattr(window, "start_ms"),
            end_ms=getattr(window, "end_ms"),
            eeg_packet_count=len(eeg_packets),
            hr_packet_count=len(hr_packets),
        )

    async def evaluate_raw(
        self,
        eeg: bytes,
        hr: bytes,
        *,
        start_ms: int,
        end_ms: int,
        eeg_packet_count: int = 0,
        hr_packet_count: int = 0,
    ) -> tuple[dict | None, list[str]]:
        """Evaluate transport-neutral bytes without requiring legacy BLE models."""

        if not eeg and not hr:
            return None, []
        process = self.process
        if not self.available or not process or not process.stdin or not process.stdout:
            return None, ["ALGORITHM_NOT_READY"]
        started_at = time.monotonic()
        try:
            request = {"timestampMs": end_ms, "windowStartMs": start_ms, "eegRawBase64": base64.b64encode(eeg).decode(), "hrRawBase64": base64.b64encode(hr).decode()}
            # Include pipe backpressure in the request budget, not just reading.
            async with asyncio.timeout(self.config.request_timeout_ms / 1000):
                process.stdin.write((json.dumps(request) + "\n").encode())
                await process.stdin.drain()
                response = await process.stdout.readline()
            if not response:
                raise RuntimeError("algorithm bridge closed stdout without a response")
            result = json.loads(response)
            bridge_error = (result.get("bridgeError") or result.get("pocError")) if isinstance(result, dict) else None
            if bridge_error:
                raise ValueError(f"algorithm bridge: {bridge_error}")
            if not isinstance(result, dict) or not isinstance(result.get("algorithm"), dict):
                LOG.warning(
                    "Algorithm bridge output invalid: timestampMs=%s eegPackets=%s hrPackets=%s responseFields=%s",
                    end_ms,
                    eeg_packet_count,
                    hr_packet_count,
                    ",".join(sorted(str(key) for key in result)) if isinstance(result, dict) else "not-an-object",
                )
                if self.process is process:
                    self.error = "algorithm bridge returned an invalid output object"
                await self._discard_exchange(process)
                return None, ["ALGORITHM_OUTPUT_INVALID"]
            LOG.debug(
                "Algorithm window evaluated: timestampMs=%s eegPackets=%s hrPackets=%s durationMs=%s outputFields=%s",
                end_ms,
                eeg_packet_count,
                hr_packet_count,
                int((time.monotonic() - started_at) * 1000),
                ",".join(sorted(str(key) for key in result["algorithm"])),
            )
            return result["algorithm"], []
        except asyncio.CancelledError:
            if self.process is process:
                self.error = "algorithm bridge request cancelled"
            await self._discard_exchange(process)
            raise
        except (asyncio.TimeoutError, json.JSONDecodeError, OSError, UnicodeError, ValueError, RuntimeError) as exc:
            if self.process is process:
                self.error = str(exc) or type(exc).__name__
            LOG.warning(
                "Algorithm bridge evaluation failed: timestampMs=%s eegPackets=%s hrPackets=%s durationMs=%s errorType=%s reason=%s",
                end_ms,
                eeg_packet_count,
                hr_packet_count,
                int((time.monotonic() - started_at) * 1000),
                type(exc).__name__,
                _safe_log_text(str(exc) or type(exc).__name__),
            )
            await self._discard_exchange(process)
            return None, ["ALGORITHM_ERROR"]
