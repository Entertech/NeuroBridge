"""Operator capture lifecycle shared by the Linux runtime and static HTTP UI."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import time
from typing import Callable

from .ble.flowtime import FlowtimeAdapter
from .runtime import GatewayRuntime
from .application.gateway import GatewayApplication as Gateway

LOG = logging.getLogger(__name__)
MAX_LOG_ENTRIES = 200
MAX_MESSAGE_CHARACTERS = 2048


class CaptureError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(message)


class CaptureLogBuffer(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.entries: deque[dict] = deque(maxlen=MAX_LOG_ENTRIES)

    def append(self, message: str, level: str = "INFO", timestamp_ms: int | None = None) -> None:
        self.acquire()
        try:
            self.entries.append({"timestampMs": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000), "level": level, "message": message[:MAX_MESSAGE_CHARACTERS]})
        finally:
            self.release()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Application operational logs only; never ingest library frame traces.
            if record.name.startswith("neurobridge"):
                self.append(record.getMessage(), record.levelname, int(record.created * 1000))
        except Exception:
            self.handleError(record)

    def snapshot(self) -> dict:
        self.acquire()
        try:
            return {"entries": list(self.entries)}
        finally:
            self.release()


def metric_summary(value):
    if isinstance(value, dict):
        return {key: metric_summary(item) for key, item in value.items() if not any(token in key.lower() for token in ("raw", "base64", "byte", "wave"))}
    if isinstance(value, (list, tuple)):
        return {"arrayLength": len(value), "valuesOmitted": True}
    return value


class CaptureControl:
    def __init__(self, gateway: Gateway, adapter_factory: Callable = FlowtimeAdapter, *, pipeline_factory: Callable | None = None, runtime: GatewayRuntime | None = None) -> None:
        self.gateway = gateway
        self.adapter_factory = adapter_factory
        self.pipeline_factory = pipeline_factory
        self.runtime = runtime or (GatewayRuntime(gateway, pipeline_factory, restart_delay_seconds=gateway.config.ble.reconnect_delay_seconds, shutdown_timeout_seconds=gateway.config.pipeline.shutdown_timeout_ms / 1000) if pipeline_factory is not None else None)
        self.logs = CaptureLogBuffer()
        self._adapter = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._closing = False
        self._stop_failed = False
        self._previous_observer = gateway.window_observer
        gateway.window_observer = self.observe_window
        logging.getLogger().addHandler(self.logs)

    @property
    def running(self) -> bool:
        return self.runtime.running if self.runtime is not None else bool(self._task and not self._task.done())

    def snapshot(self) -> dict:
        store = self.gateway.store
        export_id = store.recording_id or store.last_recording_id
        return {
            "gatewayBootId": self.gateway.boot_id,
            "subjectId": self.gateway.config.recording.subject_id,
            "mode": self.gateway.mode(),
            **self.gateway.status,
            "serverTimeMs": int(time.time() * 1000),
            "captureRunning": self.running,
            "recordingId": store.recording_id,
            "connectionError": self.gateway.connection_error,
            "exportRecordingId": export_id,
            "exportUrl": f"{self.gateway.config.download.path}/recordings/{export_id}.zip" if export_id and not store.recording_id else None,
            "websocketUrl": f"ws://{self.gateway.config.server.host}:{self.gateway.config.server.port}{self.gateway.config.server.path}",
        }

    async def start(self) -> dict:
        async with self._lock:
            if self._closing:
                raise CaptureError(503, "Capture service is shutting down.")
            if not self.gateway.config.ble.enabled:
                raise CaptureError(503, "Device capture is disabled in deployment configuration (ble.enabled=false).")
            if self.runtime is not None:
                await self.runtime.start()
                self._adapter = self.runtime.session
                return self.snapshot()
            if self._stop_failed:
                raise CaptureError(503, "Previous device stop failed; stop it successfully before restarting.")
            if not self.running:
                if self._adapter and getattr(getattr(self._adapter, "_client", None), "is_connected", False):
                    raise CaptureError(503, "Previous device link is still connected; stop it successfully before restarting.")
                self._adapter = self.pipeline_factory() if self.pipeline_factory is not None else self.adapter_factory(self.gateway.config.ble, self.gateway.receive_packet, self.gateway.update_status, self.gateway.on_device_ready, self.gateway.update_connection_error, diagnostics_interval_seconds=self.gateway.config.logging.diagnostics_interval_seconds)
                self._task = asyncio.create_task(self._run_adapter(self._adapter), name="capture-device")
                LOG.info("Operator capture started: bootId=%s", self.gateway.boot_id)
            return self.snapshot()

    async def _run_adapter(self, adapter) -> None:
        try:
            await adapter.run()
        except Exception as exc:
            LOG.exception("Capture device task failed: bootId=%s", self.gateway.boot_id)
            await self.gateway.update_connection_error(str(exc))
        finally:
            try:
                await self.gateway.update_status("connectionState", "disconnected")
            except Exception:
                LOG.exception("Capture device task cleanup failed: bootId=%s", self.gateway.boot_id)

    async def stop(self) -> dict:
        async with self._lock:
            if self.runtime is not None:
                await self.runtime.stop()
                self._adapter = None
                return self.snapshot()
            adapter, task = self._adapter, self._task
            failure = None
            try:
                if adapter:
                    async with asyncio.timeout(10):
                        await adapter.stop()
            except Exception as exc:
                failure = exc
                LOG.exception("Operator capture stop failed: bootId=%s", self.gateway.boot_id)
                await self.gateway.update_connection_error("Device capture stop could not be confirmed; inspect gateway logs.")
            finally:
                if task and not task.done():
                    task.cancel()
                if task:
                    await asyncio.gather(task, return_exceptions=True)
                self._task = None
                await self.gateway.update_status("connectionState", "disconnected")
            self._stop_failed = failure is not None
            if failure:
                raise CaptureError(503, "Device capture stop could not be confirmed; inspect gateway logs.") from failure
            self._adapter = None
            LOG.info("Operator capture stopped: bootId=%s recordingId=%s", self.gateway.boot_id, self.gateway.store.last_recording_id)
            return self.snapshot()

    async def observe_window(self, window, algorithm, reasons, valid) -> None:
        start = getattr(window, "window_start_ms", getattr(window, "start_ms", None))
        end = getattr(window, "window_end_ms", getattr(window, "end_ms", None))
        eeg_count = len(window.eeg) if hasattr(window, "eeg") else sum(s.signal_type == "eeg" for s in window.signals)
        hr_count = len(window.hr) if hasattr(window, "hr") else sum(s.signal_type == "hr" for s in window.signals)
        self.logs.append(f"Received headband data: windowStartMs={start} windowEndMs={end} eegPackets={eeg_count} hrPackets={hr_count} valid={valid}")
        self.logs.append("Algorithm output: " + json.dumps({"valid": valid, "reasons": reasons, "algorithm": metric_summary(algorithm)}, ensure_ascii=False, separators=(",", ":")))
        if self._previous_observer:
            await self._previous_observer(window, algorithm, reasons, valid)

    async def run(self) -> None:
        try:
            if self.gateway.config.ble.enabled:
                await self.start()
            await asyncio.Future()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            if self.runtime is not None:
                await self.runtime.close()
            else:
                await self.stop()
        finally:
            self.gateway.window_observer = self._previous_observer
            logging.getLogger().removeHandler(self.logs)
            self.logs.close()
