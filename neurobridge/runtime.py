"""Shared acquisition sessions and supervised gateway capture lifecycle."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable

from .adapters.observability import ProcessMetrics, RuntimeProgress
from .versioning import APPLICATION_VERSION
from .ports.raw_source import RawDataSource
from .application.service import ApplicationService
from .application.gateway import GatewayApplication as Gateway
from .profiles.resolver import DeploymentProfile
from .domain.algorithm import AlgorithmState
from .domain.raw import ParseOutcome
from .domain.status import ConnectionState, DataState, DeviceConnectionEvent

LOG = logging.getLogger(__name__)

class AcquisitionSession:
    """Run a RawDataSource through the stable application service."""

    def __init__(
        self,
        source: RawDataSource,
        application: ApplicationService,
        gateway: Gateway,
        profile: DeploymentProfile,
    ) -> None:
        self._stopping = False
        self._running = False
        self.source = source
        self.application = application
        self.gateway = gateway
        self.profile = profile
        self._ready = asyncio.Event()
        self._connected_seen = asyncio.Event()
        self._metrics = ProcessMetrics()
        self._progress = RuntimeProgress()
        self._reconnections = 0
        self._offline_since = time.monotonic()

    async def run(self) -> None:
        if self.gateway.recording_repository is None:
            raise RuntimeError("Gateway must be started before the acquisition adapter")
        self.application.bind_recording(self.gateway.recording_repository)
        self._running = True
        try:
            await self.source.start()
            async with asyncio.TaskGroup() as group:
                group.create_task(self._consume_events())
                group.create_task(self._consume_chunks())
                group.create_task(self._observe())
                if getattr(self.source, "enabled", True):
                    group.create_task(self._watch_source())
        finally:
            self._running = False

    async def _watch_source(self) -> None:
        try:
            await self.source.wait_stopped()
        except asyncio.CancelledError:
            if self._stopping or asyncio.current_task().cancelling():
                raise
            raise RuntimeError("Device producer was cancelled unexpectedly") from None
        if not self._stopping:
            raise RuntimeError("Device producer exited unexpectedly")

    async def _observe(self) -> None:
        deadline = time.monotonic()
        while True:
            self._log_metrics(event_loop_lag_ms=max(0, time.monotonic() - deadline) * 1000)
            interval = self.gateway.config.pipeline.metrics_interval_seconds
            deadline = time.monotonic() + interval
            await asyncio.sleep(interval)

    def _log_metrics(self, *, phase: str = "periodic", event_loop_lag_ms: float = 0) -> None:
        try:
            self._sample_metrics(phase=phase, event_loop_lag_ms=event_loop_lag_ms)
        except Exception:
            # Diagnostics must never terminate acquisition or mask shutdown errors.
            LOG.exception("Runtime metrics sample failed: phase=%s", phase)

    def _sample_metrics(self, *, phase: str, event_loop_lag_ms: float) -> None:
        storage = self.gateway.recording_repository.storage_status()
        source = self.source.status()
        data = self.application.data.snapshot
        self.application.data.transition(data.data_state, storage_state=storage.state,
            persistence_guaranteed=storage.persistence_guaranteed)
        metrics = {"appVersion": APPLICATION_VERSION, "profile": self.profile.profile_id,
            "gatewayBootId": self.gateway.boot_id,
            "phase": phase, "sampledAtMs": int(time.time() * 1000),
            "eventLoopLagMs": round(event_loop_lag_ms, 3),
            "algorithmState": data.algorithm_state, "recentError": data.recent_error,
            "errorStage": data.error_stage, "clients": len(self.gateway.sessions),
            "lastStorageSuccessAtMs": storage.last_success_at_ms,
            "connectionSessionId": source.connection_session_id,
            "recordingSessionId": self.gateway.store.recording_id,
            "connectionState": source.state.value,
            "dataState": data.data_state.value, "storageState": storage.state.value,
            "persistenceGuaranteed": storage.persistence_guaranteed,
            "availableBytes": storage.available_bytes, "persistenceGaps": storage.gap_count,
            "affectedFromMs": storage.affected_from_ms, "lastProducedAtMs": data.last_produced_at_ms,
            "lastPublishedAtMs": data.last_published_at_ms,
            "offlineSeconds": time.monotonic() - self._offline_since if self._offline_since is not None else 0,
            "reconnections": self._reconnections,
            "pipeline": self.application.metrics(), "delivery": dict(self.gateway.delivery_metrics),
            "requestsByAction": dict(self.gateway.requests_by_action),
            "deliveryByStream": {stream: {**values, "overwritten": self.gateway.fanout.overwrites_by_stream.get(stream, 0)}
                                 for stream, values in self.gateway.stream_metrics.items()},
            "subscriptions": sum(len(session.subscriptions) for session in self.gateway.sessions),
            "snapshotOverwrites": self.gateway.snapshot_overwrite_count,
            "pendingStreams": self.gateway.fanout.pending_count,
            "storage": dict(storage.details), "process": self._metrics.sample(),
            "droppedRawChunks": getattr(self.source, "dropped_raw_chunks", 0),
            "droppedRawBytes": getattr(self.source, "dropped_raw_bytes", 0)}
        # Only process-lifetime counters belong in interval deltas; gauges and
        # per-session counters (such as late_results) may legitimately decrease.
        counter_names = ("raw_chunks", "raw_bytes", "frames", "discarded_bytes", "batches", "published",
                         "persistence_gaps", "algorithm_backlog", "algorithm_evaluations",
                         "algorithm_duration_ms", "algorithm_valid_results", "algorithm_invalid_results",
                         "algorithm_timeout_results", "window_processing_errors", "source_gap_bytes")
        counters = {key: self.application.diagnostics.get(key, 0) for key in counter_names}
        counters.update(deliverySent=self.gateway.delivery_metrics["sent"],
                        deliveryFailed=self.gateway.delivery_metrics["failed"], reconnections=self._reconnections)
        metrics["progress"] = self._progress.sample(counters)
        LOG.info("Runtime metrics: %s", json.dumps(metrics, ensure_ascii=False, separators=(",", ":")))

    async def stop(self) -> None:
        self._stopping = True
        # Stop the producer, drain accepted bytes while consumers are alive,
        # then finish windows/algorithm work. Faulted sessions cannot be drained.
        try:
            await self.source.stop(drain=self._running)
        finally:
            try:
                await self.application.close()
            finally:
                self._log_metrics(phase="acquisition_shutdown")

    async def device_ready(self, activate_algorithm=None, *, start_stream=True) -> bool:
        """Bind the validated connection to its recording and algorithm."""

        try:
            await asyncio.wait_for(self._connected_seen.wait(), timeout=2)
        except TimeoutError:
            LOG.error("Connected event was not applied before algorithm initialization")
            return False
        source_status = self.source.status()
        connection_session_id = source_status.connection_session_id
        recording_session_id = self.gateway.store.recording_id
        if not connection_session_id or not recording_session_id:
            LOG.error(
                "Application session identifiers unavailable: connectionSessionId=%s recordingSessionId=%s",
                connection_session_id,
                recording_session_id,
            )
            return False
        existing_stream = bool(getattr(self.source, "existing_stream", False))
        prepared_state = await activate_algorithm() if activate_algorithm is not None else None
        state = await self.application.prepare_session(
            connection_session_id,
            recording_session_id,
            existing_stream=existing_stream,
            **({"start_stream": False} if not start_stream else {}),
            **({"prepared_algorithm_state": prepared_state} if prepared_state is not None else {}),
        )
        await self.gateway.update_status("algorithmState", state.value)
        usable = state == AlgorithmState.READY or existing_stream or (
            not self.application.requires_algorithm_to_start
            and self.application.data.snapshot.data_state == DataState.READY)
        if usable:
            self._ready.set()
        return state == AlgorithmState.READY or (usable and not self.application.requires_algorithm_to_start)

    async def _consume_chunks(self) -> None:
        async for chunk in self.source.chunks():
            await self._ready.wait()
            await self.application.process(chunk)

    async def _consume_events(self) -> None:
        async for event in self.source.connection_events():
            if self._stopping:
                continue
            LOG.info(
                "Runtime connection transition: gatewayBootId=%s connectionSessionId=%s "
                "recordingSessionId=%s previous=%s current=%s occurredAtMs=%s reason=%s retryable=%s",
                self.gateway.boot_id, event.connection_session_id, self.gateway.store.recording_id,
                self.application.connection.state.value, event.state.value,
                event.occurred_at_ms, event.reason, event.retryable,
            )
            if event.state == ConnectionState.CONNECTED:
                self._offline_since = None
                gateway_state = "validated" if self.profile.transport == "serial" else "connected"
                if event.reason == "preparing_capture":
                    await self.gateway.prepare_live_session()
                else:
                    await self.gateway.update_status("connectionState", gateway_state)
                await self.application.on_connection(event)
                self._connected_seen.set()
                continue
            self._ready.clear()
            if self._offline_since is None:
                self._offline_since = time.monotonic()
                self._reconnections += 1
            self._connected_seen.clear()
            await self.application.on_connection(event)
            await self.gateway.update_status("connectionState", self._gateway_state(event))

    @staticmethod
    def _gateway_state(event: DeviceConnectionEvent) -> str:
        return {
            ConnectionState.DISCOVERING: "connecting",
            ConnectionState.CONNECTING: "connecting",
            ConnectionState.VALIDATING: "validating",
            ConnectionState.VALIDATION_FAILED: "validation_failed",
            ConnectionState.RECONNECTING: "disconnected",
            ConnectionState.DISCONNECTED: "disconnected",
        }.get(event.state, "disconnected")

    @staticmethod
    def _log_diagnostics(outcome: ParseOutcome) -> None:
        for diagnostic in outcome.diagnostics:
            LOG.warning(
                "Parser flush diagnostic: kind=%s byteCount=%s discardedBytes=%s",
                diagnostic.kind,
                diagnostic.byte_count,
                outcome.discarded_bytes,
            )



class GatewayRuntime:
    """Own capture task supervision and replace single-use sessions after failure."""

    def __init__(self, gateway, session_factory: Callable, *, restart_delay_seconds=3,
                 shutdown_timeout_seconds=5) -> None:
        self.gateway = gateway
        self.session_factory = session_factory
        self.restart_delay_seconds = restart_delay_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self.session = None
        self.session_task: asyncio.Task | None = None
        self.task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stopping = False
        self._closed = False
        self._cleanup_failed = False

    @property
    def running(self) -> bool:
        return bool(self.task and not self.task.done() and self.session_task and not self.session_task.done())

    def _spawn(self) -> None:
        self.session = self.session_factory()
        self.session_task = asyncio.create_task(self.session.run(), name="neurobridge-acquisition-session")

    async def start(self) -> None:
        async with self._lock:
            if self._closed:
                raise RuntimeError("Gateway runtime is closed")
            if self._cleanup_failed:
                raise RuntimeError("Previous acquisition cleanup failed; stop it before restarting")
            if self.task and not self.task.done():
                return
            self._stopping = False
            self._spawn()
            self.task = asyncio.create_task(self._supervise(), name="neurobridge-runtime")

    async def _retire(self) -> None:
        session, task = self.session, self.session_task
        if session is None:
            return
        failure = None
        try:
            async with asyncio.timeout(self.shutdown_timeout_seconds):
                await session.stop()
        except Exception as error:
            failure = error
        finally:
            if task is not None:
                task.cancel()
                try:
                    async with asyncio.timeout(self.shutdown_timeout_seconds):
                        await asyncio.gather(task, return_exceptions=True)
                except Exception as error:
                    failure = failure or error
            try:
                async with asyncio.timeout(self.shutdown_timeout_seconds):
                    await self.gateway.update_status("connectionState", "disconnected")
            except Exception as error:
                failure = failure or error
        self._cleanup_failed = failure is not None
        if failure:
            raise RuntimeError("Acquisition cleanup failed; automatic restart suspended") from failure
        self.session = self.session_task = None

    async def _supervise(self) -> None:
        while not self._stopping:
            try:
                await asyncio.shield(self.session_task)
                if self._stopping:
                    return
                raise RuntimeError("Acquisition session exited unexpectedly")
            except asyncio.CancelledError:
                if self._stopping:
                    raise
                reason = "Acquisition session was cancelled unexpectedly"
            except Exception as error:
                while isinstance(error, BaseExceptionGroup) and error.exceptions:
                    error = error.exceptions[0]
                reason = f"{type(error).__name__}: {error}"
            await self.gateway.update_connection_error(reason)
            LOG.error("Acquisition session failed; retiring before restart: reason=%s", reason)
            try:
                await self._retire()
            except Exception as error:
                await self.gateway.update_connection_error(str(error))
                LOG.exception("Acquisition recovery suspended after cleanup failure")
                return
            await asyncio.sleep(self.restart_delay_seconds)
            if not self._stopping:
                try:
                    self._spawn()
                except Exception as error:
                    await self.gateway.update_connection_error(str(error))
                    LOG.exception("Cannot construct acquisition session")
                    return

    async def stop(self) -> None:
        async with self._lock:
            self._stopping = True
            if self.task:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
            try:
                await self._retire()
            except Exception as error:
                await self.gateway.update_connection_error(str(error))
                raise

    async def run(self, *, auto_start=True) -> None:
        try:
            if auto_start:
                await self.start()
            await asyncio.Future()
        finally:
            await self.stop()

    async def close(self) -> None:
        self._closed = True
        await self.stop()
