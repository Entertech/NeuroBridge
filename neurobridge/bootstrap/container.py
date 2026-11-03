"""The only composition root that binds profiles to concrete adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

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
from ..config import GatewayConfig
from ..domain.algorithm import AlgorithmSession, AlgorithmState
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


# Compatibility import for existing embedding callers; orchestration lives in runtime.
from ..runtime import AcquisitionSession as _ApplicationPipelineAdapter, GatewayRuntime


def build_container(config: GatewayConfig, runtime: RuntimePlatform | None = None, *, gateway=None) -> ApplicationContainer:
    profile = resolve_profile(config, runtime)
    if gateway is not None and gateway.config != config:
        raise ValueError("An acquisition restart must retain its gateway configuration and profile")
    engine = AffectiveSdkAlgorithmEngine(config.algorithm)
    if hasattr(engine, "configure_diagnostics"):
        engine.configure_diagnostics(config.logging.diagnostics_interval_seconds)
    def recording_factory():
        values = vars(config.storage).copy()
        values["queue_size"] = values.pop("writer_queue_size")
        return SegmentedRecordingRepository(config.recording.directory, shutdown_timeout_ms=config.pipeline.shutdown_timeout_ms, **values)

    archive = SegmentedArchive(config.recording.directory)
    gateway = gateway or Gateway(config, store=archive,
                      algorithm=engine, snapshots=InMemoryLatestSnapshotStore(),
                      recording_factory=recording_factory,
                      supports_replay=profile.capabilities.supports_replay,
                      replay_reader=ArchiveReplayReader(archive, cache_ttl_seconds=config.pipeline.replay_catalog_refresh_ms / 1000) if profile.capabilities.supports_replay else None,
                      project_window=project_window, wire=GatewayWireCodec(),
                      live_connection_states=frozenset({"validated" if profile.transport == "serial" else "connected"}),
                      initial_connection_state="not_connected" if profile.transport == "serial" else "disconnected")
    gateway.algorithm = engine
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
        diagnostics_interval_seconds=config.logging.diagnostics_interval_seconds,
    )
    bridge_ref: dict[str, _ApplicationPipelineAdapter] = {}

    prepared_engine = None
    prepared_state = AlgorithmState.UNAVAILABLE

    async def prepare_algorithm() -> bool:
        nonlocal prepared_engine, prepared_state
        prepared_engine = type(engine)(config.algorithm)
        if hasattr(prepared_engine, "configure_diagnostics"):
            prepared_engine.configure_diagnostics(config.logging.diagnostics_interval_seconds)
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
            start_stream=profile.transport == "serial",
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
                                      enqueue_timeout_ms=config.pipeline.source_enqueue_timeout_ms,
                                      telemetry=gateway.update_status,
                                      diagnostics_interval_seconds=config.logging.diagnostics_interval_seconds)
    adapter = _ApplicationPipelineAdapter(source, application, gateway, profile)
    application.control = getattr(source, "control", None)
    bridge_ref["bridge"] = adapter
    controller = NorthboundController(gateway)
    return ApplicationContainer(config, profile, gateway, adapter, source, parser, application, controller)


def build_runtime(container: ApplicationContainer, runtime: RuntimePlatform | None = None) -> GatewayRuntime:
    """Assemble a reusable supervisor; every restart gets a fresh acquisition session."""
    first = [container.device_adapter]
    def session_factory():
        return first.pop() if first else build_container(container.config, runtime, gateway=container.gateway).device_adapter
    delay = (container.config.ble.reconnect_delay_seconds if container.profile.transport == "bluetooth"
             else container.config.serial.reconnect_delay_seconds)
    return GatewayRuntime(container.gateway, session_factory,
                          restart_delay_seconds=delay,
                          shutdown_timeout_seconds=container.config.pipeline.shutdown_timeout_ms / 1000)
