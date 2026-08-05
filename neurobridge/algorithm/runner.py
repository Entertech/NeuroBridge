from __future__ import annotations

import asyncio
import base64
import json
import logging
from ..config import AlgorithmConfig
from ..ble.packets import DataWindow

LOG = logging.getLogger(__name__)


class AlgorithmRunner:
    """Isolates the C++ SDK behind a line-delimited JSON bridge.

    It intentionally does not call appendEEG/appendHR until the bridge command and
    the Flowtime FF31/FF51 grouping have been validated with recorded device bytes.
    """
    def __init__(self, config: AlgorithmConfig) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self.error: str | None = None

    @property
    def available(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self) -> None:
        if not self.config.enabled:
            return
        if not self.config.command:
            self.error = "algorithm.enabled requires algorithm.command"
            return
        try:
            self.process = await asyncio.create_subprocess_exec(*self.config.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        except OSError as exc:
            self.error = str(exc)
            LOG.exception("Cannot start algorithm bridge")

    async def initialize(self) -> None:
        """Create a clean SDK process for each new device connection/session."""
        await self.stop()
        self.process = None
        self.error = None
        await self.start()

    async def stop(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        self.process = None

    async def evaluate(self, window: DataWindow) -> tuple[dict | None, list[str]]:
        if not window.eeg and not window.hr_raw:
            return None, []
        if not self.available or not self.process or not self.process.stdin or not self.process.stdout:
            return None, ["ALGORITHM_NOT_READY"]
        try:
            request = {"timestampMs": window.end_ms, "eegRawBase64": base64.b64encode(b"".join(x.value for x in window.eeg)).decode(), "hrRawBase64": base64.b64encode(b"".join(x.value for x in window.hr_raw)).decode()}
            self.process.stdin.write((json.dumps(request) + "\n").encode())
            await self.process.stdin.drain()
            response = await asyncio.wait_for(self.process.stdout.readline(), timeout=2)
            result = json.loads(response)
            if not isinstance(result.get("algorithm"), dict):
                return None, ["ALGORITHM_OUTPUT_INVALID"]
            return result["algorithm"], []
        except (asyncio.TimeoutError, json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
            self.error = str(exc)
            LOG.warning("Algorithm bridge evaluation failed: %s", exc)
            return None, ["ALGORITHM_ERROR"]
