"""The only composition root that binds profiles to concrete adapters."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import logging

from ..adapters.parsers import HeadbandBleParser, HeadsetRev181Parser
from ..adapters.sources import PosixSerialSource
from ..application.acquisition import AcquisitionProcessor
from ..business.gateway import Gateway
from ..config import GatewayConfig
from ..device.packet import DevicePacket
from ..device.strategy import DeviceAdapter, create_device_adapter
from ..domain.raw import DeviceFrame, ParseOutcome
from ..domain.signal import ParsedSignal
from ..domain.status import ConnectionState
from ..ports.raw_parser import FlushReason
from ..ports.raw_source import RawDataSource
from ..ports.raw_parser import RawDataParser
from ..profiles.resolver import DeploymentProfile, RuntimePlatform, resolve_profile


@dataclass(slots=True)
class ApplicationContainer:
    config: GatewayConfig
    profile: DeploymentProfile
    gateway: Gateway
    device_adapter: DeviceAdapter
    parser: RawDataParser


LOG = logging.getLogger(__name__)


class _SerialPipelineAdapter:
    """Incremental M1 bridge: Source -> Parser -> existing Gateway use cases."""

    def __init__(self, source: RawDataSource, parser: RawDataParser, gateway: Gateway) -> None:
        self.source = source
        self.parser = parser
        self.gateway = gateway
        self._ready = asyncio.Event()
        self._processor = AcquisitionProcessor(parser, self._on_frame, self._on_signal, self._on_outcome)

    async def run(self) -> None:
        await self.source.start()
        async with asyncio.TaskGroup() as group:
            group.create_task(self._consume_events())
            group.create_task(self._consume_chunks())

    async def stop(self) -> None:
        await self.source.stop()

    async def _consume_chunks(self) -> None:
        async for chunk in self.source.chunks():
            await self._ready.wait()
            await self._processor.process(chunk)

    async def _consume_events(self) -> None:
        async for event in self.source.connection_events():
            if event.state == ConnectionState.CONNECTED:
                await self.gateway.update_status("connectionState", "validated")
                self._ready.set()
            elif event.state == ConnectionState.VALIDATING:
                self._ready.clear()
                await self.gateway.update_status("connectionState", "validating")
            elif event.state == ConnectionState.VALIDATION_FAILED:
                self._ready.clear()
                self.parser.flush(FlushReason.DISCONNECTED)
                await self.gateway.update_status("connectionState", "validation_failed")
            elif event.state in {ConnectionState.DISCONNECTED, ConnectionState.RECONNECTING}:
                self._ready.clear()
                self.parser.flush(FlushReason.DISCONNECTED)
                await self.gateway.update_status("connectionState", "disconnected")
            else:
                await self.gateway.update_status("connectionState", "connecting")

    async def _on_frame(self, frame: DeviceFrame) -> None:
        await self.gateway.receive_device_packet(DevicePacket("serial", "serial.frame", frame.raw_bytes, frame.received_at_ms))

    async def _on_signal(self, signal: ParsedSignal) -> None:
        channel = {"eeg": "ff31", "hr": "ff51"}.get(signal.signal_type)
        if channel is not None and isinstance(signal.samples, bytes):
            await self.gateway.receive_device_packet(DevicePacket("serial", channel, signal.samples, signal.received_at_ms))

    async def _on_outcome(self, outcome: ParseOutcome) -> None:
        for diagnostic in outcome.diagnostics:
            LOG.warning(
                "Serial parse diagnostic: kind=%s severity=%s byteCount=%s bufferedBytes=%s discardedBytes=%s",
                diagnostic.kind,
                diagnostic.severity,
                diagnostic.byte_count,
                outcome.buffered_bytes,
                outcome.discarded_bytes,
            )


def build_container(config: GatewayConfig, runtime: RuntimePlatform | None = None) -> ApplicationContainer:
    profile = resolve_profile(config, runtime)
    gateway = Gateway(config)
    parser: RawDataParser
    if profile.device_protocol == "headset_rev181":
        parser = HeadsetRev181Parser(config.serial.max_buffer_bytes)
        if profile.os_family == "kylin":
            source = PosixSerialSource(config.serial, gateway.on_device_ready, gateway.update_connection_error)
            adapter: DeviceAdapter = _SerialPipelineAdapter(source, parser, gateway)
        else:
            # M3 remains an explicit extension point and is not a delivered runtime.
            adapter = create_device_adapter(config, gateway)
    elif profile.device_protocol == "headband_ble":
        parser = HeadbandBleParser()
        adapter = create_device_adapter(config, gateway)
    else:
        raise ValueError(f"No parser is registered for {profile.device_protocol}")
    return ApplicationContainer(config, profile, gateway, adapter, parser)
