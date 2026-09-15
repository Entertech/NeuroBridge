"""The only composition root that binds profiles to concrete adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import json
import time

from ..adapters.algorithms import AffectiveSdkAlgorithmEngine
from ..adapters.northbound.codec import GatewayWireCodec
from ..adapters.northbound import GatewayNorthboundSink, NorthboundController
from ..adapters.parsers import HeadbandBleParser, HeadsetRev181Parser
from ..adapters.sources import BluetoothBleakSource, PosixSerialSource, WindowsSerialSource
from ..application.service import ApplicationService
from ..application.gateway import GatewayApplication as Gateway
from ..application.snapshots import InMemoryLatestSnapshotStore
from ..adapters.northbound.protocol import project_window
from ..adapters.storage.filesystem import SegmentedRecordingRepository
from ..adapters.storage.archive import SegmentedArchive, ArchiveReplayReader
from ..adapters.observability import ProcessMetrics, RuntimeProgress
from ..versioning import APPLICATION_VERSION
from ..config import GatewayConfig
from ..domain.algorithm import AlgorithmSession, AlgorithmState
from ..domain.raw import ParseOutcome
from ..domain.status import ConnectionState, DataState, DeviceConnectionEvent
from ..ports.raw_parser import RawDataParser
from ..ports.raw_source import RawDataSource
from ..profiles.resolver import DeploymentProfile, RuntimePlatform, resolve_profile


LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class ApplicationContainer:
    config: GatewayConfig
    profile: DeploymentProfile
    gateway: Gateway
    device_adapter: "_ApplicationPipelineAdapter"
    source: RawDataSource
    parser: RawDataParser
    application: ApplicationService
    northbound_controller: NorthboundController


class _ApplicationPipelineAdapter:
    """Run a RawDataSource through the stable application service."""

    def __init__(
        self,
        source: RawDataSource,
        application: ApplicationService,
        gateway: Gateway,
        profile: DeploymentProfile,
    ) -> None:
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
        await self.source.start()
        async with asyncio.TaskGroup() as group:
            group.create_task(self._consume_events())
            group.create_task(self._consume_chunks())
            group.create_task(self._observe())

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
        # Serial preparation can enable E1 before a validated session exists;
        # stop that producer first. Preserve the BLE session-control lifecycle.
        first, second = ((self.source.stop, self.application.close) if self.profile.transport == "serial"
                         else (self.application.close, self.source.stop))
        try:
            await first()
        finally:
            try:
                await second()
            finally:
                self._log_metrics(phase="acquisition_shutdown")

    async def device_ready(self, activate_algorithm=None) -> bool:
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


def build_container(config: GatewayConfig, runtime: RuntimePlatform | None = None) -> ApplicationContainer:
    profile = resolve_profile(config, runtime)
    engine = AffectiveSdkAlgorithmEngine(config.algorithm)
    def recording_factory():
        values = vars(config.storage).copy()
        values["queue_size"] = values.pop("writer_queue_size")
        return SegmentedRecordingRepository(config.recording.directory, shutdown_timeout_ms=config.pipeline.shutdown_timeout_ms, **values)

    archive = SegmentedArchive(config.recording.directory)
    gateway = Gateway(config, store=archive,
                      algorithm=engine, snapshots=InMemoryLatestSnapshotStore(),
                      recording_factory=recording_factory,
                      supports_replay=profile.capabilities.supports_replay,
                      replay_reader=ArchiveReplayReader(archive) if profile.capabilities.supports_replay else None,
                      project_window=project_window, wire=GatewayWireCodec(),
                      live_connection_states=frozenset({"validated" if profile.transport == "serial" else "connected"}),
                      initial_connection_state="not_connected" if profile.transport == "serial" else "disconnected")
    parser: RawDataParser
    if profile.device_protocol == "headset_rev181":
        parser = HeadsetRev181Parser(config.serial.max_buffer_bytes)
    elif profile.device_protocol == "headband_ble":
        parser = HeadbandBleParser()
    else:
        raise ValueError(f"No parser is registered for {profile.device_protocol}")

    application = ApplicationService(
        device_protocol=profile.device_protocol,
        requires_algorithm_to_start=profile.transport == "serial",
        transport_trace_enabled=config.recording.transport_trace_enabled,
        transport_trace_max_bytes=config.recording.transport_trace_max_bytes,
        parser=parser,
        algorithm=engine,
        snapshots=gateway.latest_snapshot,
        northbound=GatewayNorthboundSink(gateway),
        algorithm_timeout_ms=config.algorithm.request_timeout_ms,
        interval_ms=config.data_source.window_interval_ms,
        stale_after_ms=config.data_source.stale_after_ms,
        algorithm_queue_size=config.pipeline.algorithm_queue_size,
        persistence_timeout_ms=config.pipeline.persistence_timeout_ms,
        shutdown_timeout_ms=config.pipeline.shutdown_timeout_ms,
    )
    bridge_ref: dict[str, _ApplicationPipelineAdapter] = {}

    prepared_engine = None
    prepared_state = AlgorithmState.UNAVAILABLE

    async def prepare_algorithm() -> bool:
        nonlocal prepared_engine, prepared_state
        prepared_engine = type(engine)(config.algorithm)
        prepared_state = AlgorithmState.INITIALIZING
        LOG.info("Serial algorithm preparation started: parallelWithDiscovery=true")
        try:
            prepared_state = await asyncio.wait_for(
                prepared_engine.initialize(AlgorithmSession("serial-preparation", "no-recording", "headset_rev181")),
                timeout=config.algorithm.request_timeout_ms / 1000,
            )
        except Exception:
            prepared_state = AlgorithmState.ERROR
            LOG.exception("Serial algorithm preparation failed")
        LOG.info("Serial algorithm preparation completed: state=%s", prepared_state.value)
        return prepared_state == AlgorithmState.READY

    async def release_algorithm() -> None:
        nonlocal prepared_engine
        if prepared_engine is not None:
            value, prepared_engine = prepared_engine, None
            await value.close()

    async def activate_algorithm() -> AlgorithmState:
        if prepared_engine is None:
            raise RuntimeError("Serial algorithm preparation missing for validated connection")
        # Called only after queued disconnect/connected events have completed,
        # so old-session cleanup cannot close the newly adopted process.
        await engine.adopt_prepared(prepared_engine)
        return prepared_state

    async def device_ready() -> bool:
        return await bridge_ref["bridge"].device_ready(
            activate_algorithm=activate_algorithm if profile.transport == "serial" else None,
        )

    if profile.os_family in {"kylin", "windows"}:
        source_type = PosixSerialSource if profile.os_family == "kylin" else WindowsSerialSource
        source: RawDataSource = source_type(
            config.serial,
            device_ready,
            gateway.update_connection_error,
            external_control=True,
            application_control=True,
            prepare_algorithm=prepare_algorithm,
            release_algorithm=release_algorithm,
            queue_size=config.pipeline.source_queue_size,
            enqueue_timeout_ms=config.pipeline.source_enqueue_timeout_ms,
        )
    else:
        source = BluetoothBleakSource(config.ble, device_ready, gateway.update_connection_error,
                                      queue_size=config.pipeline.source_queue_size,
                                      enqueue_timeout_ms=config.pipeline.source_enqueue_timeout_ms)
    adapter = _ApplicationPipelineAdapter(source, application, gateway, profile)
    application.control = getattr(source, "control", None)
    bridge_ref["bridge"] = adapter
    controller = NorthboundController(gateway)
    return ApplicationContainer(config, profile, gateway, adapter, source, parser, application, controller)
