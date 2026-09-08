"""The only composition root that binds profiles to concrete adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import json
import time

from ..adapters.algorithms import AffectiveSdkAlgorithmEngine
from ..adapters.northbound import GatewayNorthboundSink, NorthboundController
from ..adapters.parsers import HeadbandBleParser, HeadsetRev181Parser
from ..adapters.sources import BluetoothBleakSource, PosixSerialSource, WindowsSerialSource
from ..application.service import ApplicationService
from ..application.gateway import GatewayApplication as Gateway
from ..application.snapshots import InMemoryLatestSnapshotStore
from ..adapters.northbound.protocol import project_window
from ..adapters.storage.filesystem import SegmentedRecordingRepository
from ..adapters.storage.archive import SegmentedArchive, ArchiveReplayReader
from ..adapters.observability import ProcessMetrics
from ..versioning import APPLICATION_VERSION
from ..config import GatewayConfig
from ..domain.algorithm import AlgorithmState
from ..domain.raw import ParseOutcome
from ..domain.status import ConnectionState, DeviceConnectionEvent
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
        while True:
            try:
                storage = self.gateway.recording_repository.storage_status()
                data = self.application.data.snapshot
                self.application.data.transition(data.data_state, storage_state=storage.state,
                    persistence_guaranteed=storage.persistence_guaranteed)
                metrics = {"appVersion": APPLICATION_VERSION, "profile": self.profile.profile_id,
                    "gatewayBootId": self.gateway.boot_id,
                    "connectionSessionId": self.source.status().connection_session_id,
                    "recordingSessionId": self.gateway.store.recording_id,
                    "connectionState": self.source.status().state.value,
                    "dataState": data.data_state.value, "storageState": storage.state.value,
                    "persistenceGuaranteed": storage.persistence_guaranteed,
                    "availableBytes": storage.available_bytes, "persistenceGaps": storage.gap_count,
                    "affectedFromMs": storage.affected_from_ms, "lastProducedAtMs": data.last_produced_at_ms,
                    "lastPublishedAtMs": data.last_published_at_ms,
                    "offlineSeconds": time.monotonic() - self._offline_since if self._offline_since else 0,
                    "reconnections": self._reconnections,
                    "pipeline": self.application.metrics(), "delivery": dict(self.gateway.delivery_metrics),
                    "snapshotOverwrites": self.gateway.snapshot_overwrite_count,
                    "pendingStreams": self.gateway.fanout.pending_count,
                    "storage": dict(storage.details), "process": self._metrics.sample(),
                    "droppedRawChunks": getattr(self.source, "dropped_raw_chunks", 0),
                    "droppedRawBytes": getattr(self.source, "dropped_raw_bytes", 0)}
                LOG.info("Runtime metrics: %s", json.dumps(metrics, ensure_ascii=False, separators=(",", ":")))
            except Exception:
                LOG.exception("Runtime metrics sample failed")
            await asyncio.sleep(self.gateway.config.pipeline.metrics_interval_seconds)

    async def stop(self) -> None:
        try:
            await self.application.close()
        finally:
            await self.source.stop()

    async def device_ready(self) -> bool:
        """Initialize the algorithm before Source sends its start command."""

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
        state = await self.application.prepare_session(
            connection_session_id,
            recording_session_id,
            existing_stream=existing_stream,
        )
        await self.gateway.update_status("algorithmState", state.value)
        if state == AlgorithmState.READY or self.profile.transport != "serial" or existing_stream:
            self._ready.set()
        return state == AlgorithmState.READY

    async def _consume_chunks(self) -> None:
        async for chunk in self.source.chunks():
            await self._ready.wait()
            await self.application.process(chunk)

    async def _consume_events(self) -> None:
        async for event in self.source.connection_events():
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
                      project_window=project_window)
    parser: RawDataParser
    if profile.device_protocol == "headset_rev181":
        parser = HeadsetRev181Parser(config.serial.max_buffer_bytes)
    elif profile.device_protocol == "headband_ble":
        parser = HeadbandBleParser()
    else:
        raise ValueError(f"No parser is registered for {profile.device_protocol}")

    application = ApplicationService(
        device_protocol=profile.device_protocol,
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

    async def device_ready() -> bool:
        return await bridge_ref["bridge"].device_ready()

    if profile.os_family == "kylin":
        source: RawDataSource = PosixSerialSource(
            config.serial,
            device_ready,
            gateway.update_connection_error,
            external_control=True,
            application_control=True,
            queue_size=config.pipeline.source_queue_size,
            enqueue_timeout_ms=config.pipeline.source_enqueue_timeout_ms,
        )
    elif profile.os_family == "windows":
        source = WindowsSerialSource(
            config.serial,
            device_ready,
            gateway.update_connection_error,
            external_control=True,
            application_control=True,
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
