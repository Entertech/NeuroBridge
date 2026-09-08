from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import uuid
from typing import Any

from ..algorithm.runner import AlgorithmRunner
from ..adapters.storage import SegmentedRecordingRepository
from ..application.snapshots import InMemoryLatestSnapshotStore
from ..config import GatewayConfig
from ..device.packet import DevicePacket
from ..ble.packets import DataWindow, WindowAssembler
from ..domain.algorithm import AlgorithmResult
from ..domain.result import WindowResult
from ..domain.signal import ParsedSignal, ParsedSignalBatch
from ..ports.recording import PersistenceRecord
from .recording import RecordingStore

from ..application.gateway import GatewayApplication, ClientSession, Subscription, ProtocolError, envelope, now_ms, safe_log_text, PROTOCOL_VERSION, REPLAY_NOT_AVAILABLE_REASON, STREAM_NOT_AVAILABLE_REASON
from ..adapters.northbound.protocol import project_window
from ..adapters.northbound.controller import NorthboundController

LOG = logging.getLogger(__name__)


class Gateway(GatewayApplication):
    """Compatibility facade for pre-RawChunk in-process callers and fixtures."""

    def __init__(self, config: GatewayConfig) -> None:
        super().__init__(
            config, store=RecordingStore(config.recording.directory),
            algorithm=AlgorithmRunner(config.algorithm),
            snapshots=InMemoryLatestSnapshotStore(),
            recording_factory=lambda: SegmentedRecordingRepository(
                config.recording.directory,
                queue_size=config.storage.writer_queue_size,
                **{key: value for key, value in vars(config.storage).items() if key != "writer_queue_size"},
            ),
            supports_replay=config.data_source.type == "bluetooth",
            project_window=project_window,
        )
        self.log = LOG
        self.assembler = WindowAssembler()

    async def handle(self, session: ClientSession, raw: str, send: Any) -> None:
        await NorthboundController(self).handle(session, raw, send)

    parse_request = staticmethod(NorthboundController.parse_request)

    async def stop(self) -> None:
        await self._cancel_window_flush()
        await super().stop()

    async def update_status(self, name: str, value: object) -> None:
        if name == "connectionState" and value == "disconnected" and self.status.get(name) != value:
            await self._cancel_window_flush()
            last = self.assembler.flush()
            if last:
                await self.publish_window(last)
        await super().update_status(name, value)

    async def publish_window_result(self, result: WindowResult) -> None:
        # Legacy export fixtures still use metric files. Production composition
        # persists each window only once via RecordingRepository.
        raw, refs, signals = project_window(result)
        if self.config.data_source.type == "bluetooth":
            for stream, items in signals.items():
                for item in items:
                    self.store.save_raw_packet(stream=stream, received_at_ms=item.received_at_ms,
                        window_start_ms=result.batch.window_start_ms, window_end_ms=result.batch.window_end_ms, value=item.samples)
        if result.algorithm_result.metrics:
            self.store.save_algorithm_events(algorithm=dict(result.algorithm_result.metrics),
                computed_at_ms=result.completed_at_ms, eeg_source=refs["eeg"], hr_source=refs["hr"],
                valid=result.valid, invalid_reasons=list(result.algorithm_result.invalid_reasons))
        await super().publish_window_result(result)

    async def on_device_ready(self) -> bool:
        """Prepare a fresh local algorithm session and report whether it is usable."""
        LOG.info(
            "Local algorithm preparation started: transport=%s algorithmEnabled=%s",
            self.config.data_source.type,
            self.config.algorithm.enabled,
        )
        try:
            await self.algorithm.initialize()
        except Exception as exc:
            await self.update_status("algorithmState", "error")
            LOG.exception(
                "Local algorithm preparation failed: transport=%s algorithmState=error "
                "errorType=%s reason=%s",
                self.config.data_source.type,
                type(exc).__name__,
                safe_log_text(exc),
            )
            return False
        algorithm_state = "ready" if self.algorithm.available else ("error" if self.algorithm.error else "unavailable")
        await self.update_status("algorithmState", algorithm_state)
        if algorithm_state == "ready":
            LOG.info("Local algorithm preparation succeeded: transport=%s algorithmState=ready", self.config.data_source.type)
            return True
        reason = self.algorithm.error or (
            "algorithm_disabled_by_configuration"
            if not self.config.algorithm.enabled
            else "algorithm_process_not_available"
        )
        LOG.error(
            "Local algorithm preparation failed: transport=%s algorithmState=%s reason=%s",
            self.config.data_source.type,
            algorithm_state,
            safe_log_text(reason),
        )
        return False

    async def receive_packet(self, characteristic: str, value: bytes) -> None:
        """Compatibility entry point for tests and older in-process callers."""

        await self.receive_device_packet(DevicePacket(self.config.data_source.type, characteristic, bytes(value), now_ms()))

    async def receive_device_packet(self, packet: DevicePacket) -> None:
        """Consume the transport-neutral event emitted by a selected adapter."""

        if packet.transport == "serial" and self.status["connectionState"] != "validated":
            LOG.warning(
                "Serial device packet blocked before validation success: connectionState=%s channel=%s bytes=%s",
                self.status["connectionState"],
                packet.channel,
                len(packet.value),
            )
            return

        characteristic = packet.channel
        value = packet.value
        received_at_ms = packet.received_at_ms
        self.store.save_device_packet(
            transport=packet.transport,
            channel=characteristic,
            received_at_ms=received_at_ms,
            value=value,
        )
        if (
            self.recording_repository is not None
            and self.store.recording_id
            and (characteristic == "serial.frame" or packet.transport == "bluetooth")
        ):
            receipt = self.recording_repository.try_append(
                PersistenceRecord(
                    1,
                    self.store.recording_id,
                    "raw.device_frame",
                    received_at_ms,
                    f"raw-{uuid.uuid4().hex}",
                    {
                        "transport": packet.transport,
                        "channel": characteristic,
                        "rawBytes": value,
                    },
                )
            )
            if not receipt.accepted:
                self.status["storageState"] = self.recording_repository.storage_status().state.value
                LOG.error(
                    "Raw device frame was not accepted by persistence: recordingId=%s reason=%s",
                    self.store.recording_id,
                    receipt.reason,
                )
        raw_stream = {"ff31": "eeg", "ff51": "hr"}.get(characteristic)
        if raw_stream is None:
            if characteristic not in {"ff32", "serial.frame"}:
                LOG.warning(
                    "Ignoring unsupported device channel: transport=%s channel=%s bytes=%s",
                    packet.transport,
                    characteristic,
                    len(value),
                )
            return
        self._capture_stats[f"{raw_stream}Packets"] = int(self._capture_stats[f"{raw_stream}Packets"] or 0) + 1
        self._capture_stats[f"{raw_stream}Bytes"] = int(self._capture_stats[f"{raw_stream}Bytes"] or 0) + len(value)
        if self._capture_stats["firstPacketAtMs"] is None:
            self._capture_stats["firstPacketAtMs"] = received_at_ms
        self._capture_stats["lastDataAtMs"] = received_at_ms
        expected_bytes = {"eeg": 20, "hr": 1}[raw_stream]
        if len(value) != expected_bytes:
            self._capture_stats["invalidPacketLengths"] = int(self._capture_stats["invalidPacketLengths"] or 0) + 1
            LOG.warning(
                "Device packet length invalid: channel=%s bytes=%s expectedBytes=%s invalidPacketLengths=%s",
                characteristic,
                len(value),
                expected_bytes,
                self._capture_stats["invalidPacketLengths"],
            )
        if raw_stream:
            window_start_ms = received_at_ms - received_at_ms % self.assembler.interval_ms
            self.store.save_raw_packet(
                stream=raw_stream,
                received_at_ms=received_at_ms,
                window_start_ms=window_start_ms,
                window_end_ms=window_start_ms + self.assembler.interval_ms,
                value=value,
            )
        for window in self.assembler.add(characteristic, value, received_at_ms):
            await self.publish_window(window)
        self._schedule_window_flush()

    def _schedule_window_flush(self) -> None:
        deadline = self.assembler.window_end_ms
        if deadline is None:
            return
        if self._window_flush_task and not self._window_flush_task.done() and self._window_flush_deadline_ms == deadline:
            return
        if self._window_flush_task and not self._window_flush_task.done():
            self._window_flush_task.cancel()
        self._window_flush_deadline_ms = deadline
        self._window_flush_task = asyncio.create_task(self._flush_window_at(deadline))

    async def _flush_window_at(self, deadline_ms: int) -> None:
        try:
            await asyncio.sleep(max(0, deadline_ms - now_ms()) / 1000)
            if self._window_flush_deadline_ms != deadline_ms:
                return
            for window in self.assembler.flush_until(deadline_ms):
                await self.publish_window(window)
        except asyncio.CancelledError:
            return
        finally:
            if self._window_flush_task is asyncio.current_task():
                self._window_flush_task = None
                self._window_flush_deadline_ms = None

    async def _cancel_window_flush(self) -> None:
        task, self._window_flush_task = self._window_flush_task, None
        self._window_flush_deadline_ms = None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def publish_window(self, window: DataWindow) -> None:
        self._capture_stats["windows"] = int(self._capture_stats["windows"] or 0) + 1
        raw = window.raw_payload()
        reasons = list(window.reasons)
        algorithm_payload, algorithm_reasons = await self.algorithm.evaluate(window)
        if self.algorithm.error and self.status["algorithmState"] != "error":
            await self.update_status("algorithmState", "error")
        # A disabled/unready algorithm does not invalidate correctly received raw data.
        if self.algorithm.available:
            reasons.extend(algorithm_reasons)
        valid = not reasons
        batch_id = f"batch-{uuid.uuid4().hex}"
        persistence_guaranteed = True
        if self.recording_repository is not None and self.store.recording_id:
            parsed_receipt = self.recording_repository.try_append(
                PersistenceRecord(
                    1,
                    self.store.recording_id,
                    "parsed.signal_batch",
                    window.end_ms,
                    batch_id,
                    {
                        "batchId": batch_id,
                        "windowStartMs": window.start_ms,
                        "windowEndMs": window.end_ms,
                        "eegPacketCount": len(window.eeg),
                        "hrPacketCount": len(window.hr),
                        "valid": valid,
                        "invalidReasons": reasons,
                    },
                )
            )
            parsed_receipt = await self.recording_repository.confirm(parsed_receipt)
            persistence_guaranteed = parsed_receipt.persistence_guaranteed
        if not valid:
            self._capture_stats["invalidWindows"] = int(self._capture_stats["invalidWindows"] or 0) + 1
        LOG.debug(
            "Capture window processed: recordingId=%s startMs=%s endMs=%s eegPackets=%s hrPackets=%s "
            "valid=%s invalidReasons=%s algorithmState=%s clients=%s subscriptions=%s",
            self.store.recording_id,
            window.start_ms,
            window.end_ms,
            len(window.eeg),
            len(window.hr),
            valid,
            ",".join(reasons) if reasons else "none",
            self.status.get("algorithmState"),
            len(self.sessions),
            sum(len(session.subscriptions) for session in self.sessions),
        )
        now = now_ms()
        last_summary = int(self._capture_stats["lastSummaryAtMs"] or 0)
        if now - last_summary >= 10_000:
            self._capture_stats["lastSummaryAtMs"] = now
            self._log_capture_summary("periodic")
        if algorithm_payload and self.config.data_source.type == "bluetooth":
            self.store.save_algorithm_events(
                algorithm=algorithm_payload,
                computed_at_ms=now_ms(),
                eeg_source=self.store.source_reference(window.eeg, window_start_ms=window.start_ms, window_end_ms=window.end_ms),
                hr_source=self.store.source_reference(window.hr, window_start_ms=window.start_ms, window_end_ms=window.end_ms),
                valid=valid,
                invalid_reasons=reasons,
            )
            if valid:
                self.latest_algorithm, self.latest_algorithm_timestamp = algorithm_payload, window.end_ms
        if self.recording_repository is not None and self.store.recording_id:
            algorithm_receipt = self.recording_repository.try_append(
                PersistenceRecord(
                    1,
                    self.store.recording_id,
                    "algorithm.result",
                    now_ms(),
                    batch_id,
                    {
                        "batchId": batch_id,
                        "metrics": algorithm_payload or {},
                        "valid": valid,
                        "invalidReasons": reasons,
                    },
                )
            )
            algorithm_receipt = await self.recording_repository.confirm(algorithm_receipt)
            persistence_guaranteed = persistence_guaranteed and algorithm_receipt.persistence_guaranteed
            storage_status = self.recording_repository.storage_status()
            self.status["storageState"] = storage_status.state.value
            self.status["persistenceGuaranteed"] = persistence_guaranteed
            if not persistence_guaranteed:
                LOG.error(
                    "Window persistence is not guaranteed: recordingId=%s batchId=%s storageState=%s",
                    self.store.recording_id,
                    batch_id,
                    storage_status.state.value,
                )
        recording_session_id = self.store.recording_id or "unrecorded"
        snapshot_signals: list[ParsedSignal] = []
        for signal_type, packets, expected_bytes in (
            ("eeg", window.eeg, 20),
            ("hr", window.hr, 1),
        ):
            if not packets:
                continue
            signal_reasons = tuple(
                dict.fromkeys(
                    f"{signal_type.upper()}_PACKET_LENGTH_INVALID"
                    for packet in packets
                    if len(packet.value) != expected_bytes
                )
            )
            snapshot_signals.append(
                ParsedSignal(
                    signal_type,
                    b"".join(packet.value for packet in packets),
                    "bytes",
                    None,
                    {"packet_bytes": expected_bytes, "packet_count": len(packets)},
                    (),
                    window.end_ms,
                    not signal_reasons,
                    signal_reasons,
                )
            )
        snapshot_batch = ParsedSignalBatch(
            batch_id,
            "headset_rev181" if self.config.data_source.type == "serial" else "headband_ble",
            "legacy-connection-session",
            recording_session_id,
            window.start_ms,
            window.end_ms,
            tuple(snapshot_signals),
            (),
            valid,
            tuple(reasons),
        )
        completed_at_ms = now_ms()
        snapshot_algorithm = AlgorithmResult(
            batch_id,
            None,
            completed_at_ms,
            completed_at_ms,
            algorithm_payload or {},
            valid,
            tuple(reasons),
        )
        self.latest_snapshot.replace(
            WindowResult(snapshot_batch, snapshot_algorithm, "live", completed_at_ms, persistence_guaranteed)
        )
        if self.window_observer:
            try:
                await self.window_observer(window, algorithm_payload, reasons, valid)
            except Exception:
                LOG.exception("Capture window observer failed")
        for session in tuple(self.sessions):
            for subscription in tuple(session.subscriptions.values()):
                payload = self.filtered_payload(raw, algorithm_payload, subscription.streams)
                if not payload or (not valid and not subscription.include_invalid):
                    continue
                if not valid:
                    payload["invalidReasons"] = reasons
                self._queue_live_message(
                    session,
                    subscription,
                    envelope(200, self.event_data("data", subscription.id, window.end_ms, "live", valid, payload)),
                )
        # Let ready send tasks run without awaiting client I/O. A slow client
        # remains isolated behind its single latest-value slot.
        await asyncio.sleep(0)
