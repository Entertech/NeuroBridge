"""Production orchestration for raw acquisition, parsing, algorithms, and snapshots."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from collections.abc import Callable

from ..domain.algorithm import AlgorithmResult, AlgorithmSession, AlgorithmState
from ..domain.raw import DeviceFrame, ParseOutcome, RawChunk
from ..domain.result import WindowResult
from ..domain.signal import ParsedSignal, ParsedSignalBatch
from ..domain.status import ConnectionState, DataState, DeviceConnectionEvent
from ..ports.algorithm import AlgorithmEngine
from ..ports.device_control import DeviceControl
from ..ports.northbound import ApplicationEvent, LatestSnapshotStore, NorthboundSink
from ..ports.raw_parser import FlushReason, RawDataParser
from ..ports.recording import PersistenceReceipt, PersistenceRecord, RecordingRepository
from .processing import AlgorithmInputMapper, WindowResultAggregator
from .status import ConnectionStateMachine, DataStateMachine
from .windowing import SignalWindowAssembler


LOG = logging.getLogger(__name__)


class ApplicationService:
    """Own the transport-neutral live-data path.

    The service deliberately knows no serial, BLE, WebSocket, filesystem, or SDK
    implementation.  Adapters are supplied through ports by the composition root.
    """

    def __init__(
        self,
        *,
        device_protocol: str,
        requires_algorithm_to_start: bool = True,
        transport_trace_enabled: bool = False,
        transport_trace_max_bytes: int = 1024 * 1024,
        parser: RawDataParser,
        algorithm: AlgorithmEngine,
        snapshots: LatestSnapshotStore,
        northbound: NorthboundSink,
        algorithm_timeout_ms: int = 2000,
        interval_ms: int = 600,
        stale_after_ms: int = 1800,
        recording: RecordingRepository | None = None,
        clock_ms: Callable[[], int] | None = None,
        control: DeviceControl | None = None,
        algorithm_queue_size: int = 8,
        persistence_timeout_ms: int = 100,
        shutdown_timeout_ms: int = 5000,
    ) -> None:
        self.device_protocol = device_protocol
        self.requires_algorithm_to_start = requires_algorithm_to_start
        self.transport_trace_enabled = transport_trace_enabled
        self.transport_trace_max_bytes = transport_trace_max_bytes
        self._trace_bytes = 0
        self.parser = parser
        self.algorithm = algorithm
        self.snapshots = snapshots
        self.northbound = northbound
        self.recording = recording
        self.control = control
        if min(algorithm_queue_size, persistence_timeout_ms, shutdown_timeout_ms) <= 0:
            raise ValueError("Pipeline limits must be positive")
        self.persistence_timeout_ms = persistence_timeout_ms
        self.shutdown_timeout_ms = shutdown_timeout_ms
        self._batches: asyncio.Queue[tuple[ParsedSignalBatch, PersistenceReceipt]] = asyncio.Queue(algorithm_queue_size)
        self._worker: asyncio.Task[None] | None = None
        self._batch_sequence = 0
        self._batch_order: dict[str, int] = {}
        self._last_published_order = 0
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.connection = ConnectionStateMachine()
        self.data = DataStateMachine()
        self.assembler = SignalWindowAssembler(interval_ms)
        self.mapper = AlgorithmInputMapper()
        self.aggregator = WindowResultAggregator(algorithm, algorithm_timeout_ms)
        if stale_after_ms <= interval_ms:
            raise ValueError("stale_after_ms must be greater than interval_ms")
        self.stale_after_ms = stale_after_ms
        self._frames: dict[str, DeviceFrame] = {}
        self._frame_receipts: dict[str, PersistenceReceipt] = {}
        self._recording_session_id: str | None = None
        self._connection_session_id: str | None = None
        self._algorithm_state = AlgorithmState.UNAVAILABLE
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_deadline_ms: int | None = None
        self._stale_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._closed = False
        self._last_frame_monotonic: float | None = None
        self._last_frame_at_ms: int | None = None
        self._last_window_start_ms: int | None = None
        self._last_window_end_ms: int | None = None
        self.diagnostics: dict[str, int] = {
            "raw_chunks": 0,
            "raw_bytes": 0,
            "frames": 0,
            "discarded_bytes": 0,
            "batches": 0,
            "published": 0,
            "persistence_gaps": 0,
            "algorithm_backlog": 0,
            "queue_high_water": 0,
            "superseded_results": 0,
            "algorithm_duration_ms": 0,
            "algorithm_evaluations": 0,
            "algorithm_max_duration_ms": 0,
            "algorithm_valid_results": 0,
            "algorithm_invalid_results": 0,
            "algorithm_timeout_results": 0,
            "window_processing_errors": 0,
            "publication_age_ms": 0,
        }

    def bind_recording(self, recording: RecordingRepository) -> None:
        """Bind the repository created at process start, before acquisition starts."""

        if self.recording is not None and self.recording is not recording:
            raise RuntimeError("Application recording repository is already bound")
        self.recording = recording

    def metrics(self) -> dict[str, int | None]:
        return {**self.diagnostics, "algorithm_queue_depth": self._batches.qsize(),
                "algorithm_queue_capacity": self._batches.maxsize,
                "retained_frames": len(self._frames), "late_results": self.aggregator.late_result_count,
                "last_frame_at_ms": self._last_frame_at_ms,
                "last_frame_age_ms": (int(max(0, time.monotonic() - self._last_frame_monotonic) * 1000)
                                      if self._last_frame_monotonic is not None else None),
                "last_window_start_ms": self._last_window_start_ms,
                "last_window_end_ms": self._last_window_end_ms}

    async def on_connection(self, event: DeviceConnectionEvent) -> None:
        """Apply source events to the independent connection and data machines."""

        if event.state == self.connection.state:
            return
        try:
            applied = self.connection.transition(
                event.state,
                event.occurred_at_ms,
                connection_session_id=event.connection_session_id,
                existing_stream=event.existing_stream,
                reason=event.reason,
                retryable=event.retryable,
            )
        except ValueError:
            # A source may reconnect after process startup and omit historical
            # intermediate states.  Reset only the state-machine cursor; never
            # synthesize device data or identifiers.
            LOG.warning(
                "Connection event sequence reset: previous=%s current=%s",
                self.connection.state.value,
                event.state.value,
            )
            self.connection = ConnectionStateMachine()
            if event.state != ConnectionState.DISCONNECTED:
                for state in self._path_from_disconnected(event.state):
                    applied = self.connection.transition(
                        state,
                        event.occurred_at_ms,
                        connection_session_id=event.connection_session_id if state == ConnectionState.CONNECTED else None,
                        existing_stream=event.existing_stream if state == ConnectionState.CONNECTED else False,
                        reason=event.reason,
                        retryable=event.retryable,
                    )
            else:
                applied = event
        if event.state == ConnectionState.CONNECTED:
            async with self._lifecycle_lock:
                self._connection_session_id = event.connection_session_id
                if self.data.snapshot.data_state == DataState.UNAVAILABLE:
                    self.data.on_connection(applied)
            return
        if event.state in {ConnectionState.DISCONNECTED, ConnectionState.RECONNECTING, ConnectionState.VALIDATION_FAILED}:
            # Invalidate the connection generation before awaiting any flush or
            # in-flight algorithm work. Results originating from the previous
            # device session must never publish into the disconnected/new one.
            async with self._lifecycle_lock:
                self._connection_session_id = None
                if self.data.snapshot.data_state != DataState.UNAVAILABLE:
                    self.data.on_connection(applied)
            await self._cancel_stale()
            await self.flush(FlushReason.DISCONNECTED, drain=False)
            await self._stop_worker()
            await self.aggregator.close()

    async def prepare_session(
        self,
        connection_session_id: str,
        recording_session_id: str,
        *,
        existing_stream: bool = False,
        prepared_algorithm_state: AlgorithmState | None = None,
    ) -> AlgorithmState:
        """Bind a clean algorithm session, optionally reusing an unused prepared engine."""

        async with self._lifecycle_lock:
            self._connection_session_id = connection_session_id
            self._recording_session_id = recording_session_id
            if self.data.snapshot.data_state == DataState.UNAVAILABLE:
                self.data.transition(DataState.PREPARING)
            elif self.data.snapshot.data_state in {DataState.ERROR, DataState.PREPARING}:
                self.data.transition(DataState.PREPARING)
            self._algorithm_state = AlgorithmState.INITIALIZING
        # A new device session must not share an in-flight SDK evaluation with
        # its predecessor. Stop the bounded worker before reinitializing it.
        await self._stop_worker()
        await self.aggregator.close()
        self.aggregator = WindowResultAggregator(self.algorithm, self.aggregator.timeout_ms)
        try:
            # A serial source may have warmed an unused engine in parallel with
            # discovery. Bootstrap transfers it only after frame validation.
            algorithm_state = prepared_algorithm_state
            if algorithm_state is None:
                algorithm_state = await self.algorithm.initialize(
                    AlgorithmSession(connection_session_id, recording_session_id, self.device_protocol)
                )
        except Exception as error:
            LOG.exception(
                "Algorithm session initialization failed: connectionSessionId=%s recordingSessionId=%s",
                connection_session_id,
                recording_session_id,
            )
            algorithm_state = AlgorithmState.ERROR
        async with self._lifecycle_lock:
            self._algorithm_state = algorithm_state
            if self._connection_session_id == connection_session_id:
                if self._algorithm_state == AlgorithmState.READY:
                    self.data.transition(DataState.READY, algorithm_state=self._algorithm_state.value)
                else:
                    self.data.transition(
                        DataState.ERROR if self.requires_algorithm_to_start and self.control is not None and not existing_stream else DataState.READY,
                        algorithm_state=self._algorithm_state.value,
                        recent_error="ALGORITHM_NOT_READY",
                        error_stage="algorithm_initialize",
                    )
        LOG.info(
            "Application session prepared: connectionSessionId=%s recordingSessionId=%s algorithmState=%s existingStream=%s",
            connection_session_id,
            recording_session_id,
            self._algorithm_state.value,
            existing_stream,
        )
        if self.control is not None:
            if connection_session_id != self._connection_session_id:
                return AlgorithmState.ERROR
            if algorithm_state == AlgorithmState.READY or existing_stream or not self.requires_algorithm_to_start:
                result = await self.control.start_stream(connection_session_id)
                if result.outcome not in {"started", "alreadyStreaming"}:
                    if connection_session_id == self._connection_session_id:
                        self.data.transition(DataState.ERROR, recent_error=result.reason or result.outcome, error_stage="control")
                    return AlgorithmState.ERROR
            elif self.requires_algorithm_to_start:
                self.data.transition(DataState.ERROR, recent_error="ALGORITHM_NOT_READY", error_stage="algorithm_initialize")
        return self._algorithm_state

    async def process(self, chunk: RawChunk) -> ParseOutcome:
        if self._closed:
            raise RuntimeError("ApplicationService is closed")
        if chunk.connection_session_id != self._connection_session_id:
            LOG.warning(
                "Stale RawChunk rejected: currentConnectionSessionId=%s chunkConnectionSessionId=%s",
                self._connection_session_id,
                chunk.connection_session_id,
            )
            return ParseOutcome()
        if self.transport_trace_enabled:
            if self._trace_bytes + len(chunk.data) <= self.transport_trace_max_bytes:
                self._trace_bytes += len(chunk.data)
                self._persist(PersistenceRecord(1, self._required_recording_id(), "raw.transport_chunk",
                    chunk.received_at_ms, chunk.trace_id,
                    {"rawBytes": chunk.data, "sourceType": chunk.source_type, "channel": chunk.channel,
                     "connectionSessionId": chunk.connection_session_id,
                     "receivedAtMonotonicNs": chunk.received_at_monotonic_ns,
                     "droppedBeforeBytes": chunk.dropped_before_bytes}))
            else:
                self.diagnostics["trace_omitted_bytes"] = self.diagnostics.get("trace_omitted_bytes", 0) + len(chunk.data)
        self.diagnostics["raw_chunks"] += 1
        self.diagnostics["raw_bytes"] += len(chunk.data)
        if chunk.dropped_before_bytes:
            # Never splice an old partial frame across a known acquisition gap.
            await self.flush(FlushReason.RESET, drain=False)
            self.diagnostics["source_gap_bytes"] = self.diagnostics.get("source_gap_bytes", 0) + chunk.dropped_before_bytes
            self.data.transition(self.data.snapshot.data_state, recent_error="SOURCE_QUEUE_GAP", error_stage="acquisition")
        outcome = self.parser.feed(chunk)
        if chunk.dropped_before_bytes:
            outcome = replace(outcome, signals=tuple(replace(signal, valid=False,
                invalid_reasons=tuple(dict.fromkeys((*signal.invalid_reasons, "SOURCE_QUEUE_GAP")))) for signal in outcome.signals))
        self.diagnostics["frames"] += len(outcome.frames)
        if outcome.frames:
            self._last_frame_monotonic = time.monotonic()
            self._last_frame_at_ms = outcome.frames[-1].received_at_ms
        self.diagnostics["discarded_bytes"] += outcome.discarded_bytes
        for diagnostic in outcome.diagnostics:
            key = "parser_" + diagnostic.kind
            self.diagnostics[key] = self.diagnostics.get(key, 0) + 1
            LOG.log(
                logging.ERROR if diagnostic.severity == "error" else logging.WARNING,
                "Parser diagnostic: kind=%s byteCount=%s bufferedBytes=%s discardedBytes=%s connectionSessionId=%s",
                diagnostic.kind,
                diagnostic.byte_count,
                outcome.buffered_bytes,
                outcome.discarded_bytes,
                chunk.connection_session_id,
            )
        for frame in outcome.frames:
            self._frames[frame.frame_id] = frame
            self._frame_receipts[frame.frame_id] = self._persist(
                PersistenceRecord(
                    1,
                    self._required_recording_id(),
                    "raw.device_frame",
                    frame.received_at_ms,
                    frame.frame_id,
                    {
                        "frameId": frame.frame_id,
                        "deviceProtocol": frame.device_protocol,
                        "connectionSessionId": frame.connection_session_id,
                        "sequence": frame.sequence,
                        "sourceChunkIds": frame.source_chunk_ids,
                        "rawBytes": frame.raw_bytes,
                    },
                )
            )
        for signal in outcome.signals:
            batches = self.assembler.append(
                signal,
                device_protocol=self.device_protocol,
                connection_session_id=chunk.connection_session_id,
                recording_session_id=self._required_recording_id(),
                source_type=chunk.source_type,
            )
            for batch in batches:
                await self._submit_batch(batch)
        self._schedule_flush()
        return outcome

    async def flush(self, reason: FlushReason = FlushReason.STOPPED, *, drain: bool = True) -> None:
        await self._cancel_flush()
        parser_outcome = self.parser.flush(reason)
        for diagnostic in parser_outcome.diagnostics:
            LOG.warning("Parser flushed: kind=%s discardedBytes=%s reason=%s", diagnostic.kind, parser_outcome.discarded_bytes, reason.value)
        self.diagnostics["discarded_bytes"] += parser_outcome.discarded_bytes
        batch = self.assembler.flush()
        if batch is not None:
            await self._submit_batch(batch)
        if drain:
            await self.drain()

    async def drain(self) -> None:
        """Finite barrier for shutdown and tests; never used by acquisition."""
        try:
            await asyncio.wait_for(self._batches.join(), self.shutdown_timeout_ms / 1000)
        except TimeoutError:
            LOG.error("Pipeline drain timed out: queuedBatches=%s", self._batches.qsize())
            await self._stop_worker()

    async def _stop_worker(self) -> None:
        task, self._worker = self._worker, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        while not self._batches.empty():
            batch, _receipt = self._batches.get_nowait()
            self._persist_algorithm(batch, self.aggregator._invalid(batch.batch_id, "CONNECTION_SESSION_ENDED"), late=False)
            self._discard_batch_frames(batch)
            self._batches.task_done()

    async def _submit_batch(self, batch: ParsedSignalBatch) -> None:
        self._batch_sequence += 1
        self._batch_order[batch.batch_id] = self._batch_sequence
        receipt = self._persist_parsed(batch)
        try:
            self._batches.put_nowait((batch, receipt))
        except asyncio.QueueFull:
            self.diagnostics["algorithm_backlog"] += 1
            await self._process_batch(batch, receipt, backlog=True)
            return
        self.diagnostics["queue_high_water"] = max(self.diagnostics["queue_high_water"], self._batches.qsize())
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._process_queued(), name="neurobridge-algorithm-worker")

    async def _process_queued(self) -> None:
        while True:
            batch, receipt = await self._batches.get()
            try:
                await self._process_batch(batch, receipt)
            except Exception:
                self.diagnostics["window_processing_errors"] += 1
                LOG.exception("Window processing failed: batchId=%s", batch.batch_id)
            finally:
                self._discard_batch_frames(batch)
                self._batches.task_done()

    async def close(self) -> None:
        if self._closed:
            return
        if self.control is not None and self._connection_session_id is not None:
            try:
                await asyncio.wait_for(self.control.stop_stream(self._connection_session_id), self.shutdown_timeout_ms / 1000)
            except (Exception, TimeoutError):
                LOG.exception("Device stop failed during application shutdown")
        await self.flush(FlushReason.STOPPED)
        await self._stop_worker()
        await self.aggregator.close()
        await self._cancel_stale()
        await self.algorithm.close()
        self._closed = True

    def _persist_parsed(self, batch: ParsedSignalBatch) -> PersistenceReceipt:
        return self._persist(
            PersistenceRecord(
                1,
                batch.recording_session_id,
                "parsed.signal_batch",
                batch.window_end_ms,
                batch.batch_id,
                {
                    "batchId": batch.batch_id,
                    "schemaVersion": batch.schema_version,
                    "sourceType": batch.source_type,
                    "sampleCounts": batch.sample_counts,
                    "sequenceRange": batch.sequence_range,
                    "receivedAtRangeMs": batch.received_at_range_ms,
                    "connectionSessionId": batch.connection_session_id,
                    "deviceProtocol": batch.device_protocol,
                    "windowStartMs": batch.window_start_ms,
                    "windowEndMs": batch.window_end_ms,
                    "frameRefs": batch.frame_refs,
                    "signals": tuple(self._signal_value(signal) for signal in batch.signals),
                    "valid": batch.valid,
                    "invalidReasons": batch.invalid_reasons,
                },
            )
        )
    async def _process_batch(self, batch: ParsedSignalBatch, parsed_receipt: PersistenceReceipt, *, backlog: bool = False) -> WindowResult:
        self.diagnostics["batches"] += 1
        persistence_guaranteed = parsed_receipt.persistence_guaranteed
        if batch.connection_session_id != self._connection_session_id:
            timestamp = self.clock_ms()
            stale_result = WindowResult(
                batch,
                AlgorithmResult(
                    batch.batch_id,
                    None,
                    timestamp,
                    timestamp,
                    {},
                    False,
                    ("CONNECTION_SESSION_ENDED",),
                ),
                "live",
                timestamp,
                False,
            )
            stale_receipts = [parsed_receipt]
            stale_receipts.extend(
                self._frame_receipts[frame_ref]
                for frame_ref in batch.frame_refs
                if frame_ref in self._frame_receipts
            )
            self._discard_batch_frames(batch)
            LOG.info("Window discarded before evaluation because its connection session ended: batchId=%s", batch.batch_id)
            return stale_result
        if backlog:
            result = WindowResult(batch, self.aggregator._invalid(batch.batch_id, "ALGORITHM_BACKLOG"), "live", self.clock_ms(), False)
        elif self._algorithm_state in {AlgorithmState.READY, AlgorithmState.ERROR}:
            evaluation_started = time.monotonic()
            algorithm_input = self.mapper.map(batch, (self._frames[ref] for ref in batch.frame_refs if ref in self._frames))
            result = await self.aggregator.evaluate(
                batch,
                algorithm_input,
                persistence_guaranteed=persistence_guaranteed,
                on_late_result=lambda value: self._persist_late(batch, value),
            )
            duration_ms = int((time.monotonic() - evaluation_started) * 1000)
            self.diagnostics["algorithm_duration_ms"] += duration_ms
            self.diagnostics["algorithm_evaluations"] += 1
            self.diagnostics["algorithm_max_duration_ms"] = max(self.diagnostics["algorithm_max_duration_ms"], duration_ms)
        else:
            timestamp = self.clock_ms()
            unavailable = AlgorithmResult(
                batch.batch_id,
                None,
                timestamp,
                timestamp,
                {},
                False,
                ("ALGORITHM_UNAVAILABLE",),
            )
            result = WindowResult(batch, unavailable, "live", timestamp, persistence_guaranteed)
        result_counter = "algorithm_valid_results" if result.algorithm_result.valid else "algorithm_invalid_results"
        self.diagnostics[result_counter] += 1
        if "ALGORITHM_TIMEOUT" in result.algorithm_result.invalid_reasons:
            self.diagnostics["algorithm_timeout_results"] += 1
        if batch.connection_session_id != self._connection_session_id:
            LOG.info(
                "Stale window result discarded: batchId=%s batchConnectionSessionId=%s currentConnectionSessionId=%s",
                batch.batch_id,
                batch.connection_session_id,
                self._connection_session_id,
            )
            stale_receipts = [parsed_receipt]
            stale_receipts.extend(
                self._frame_receipts[frame_ref]
                for frame_ref in batch.frame_refs
                if frame_ref in self._frame_receipts
            )
            self._discard_batch_frames(batch)
            return result
        receipts = [parsed_receipt]
        receipts.extend(
            self._frame_receipts[frame_ref]
            for frame_ref in batch.frame_refs
            if frame_ref in self._frame_receipts
        )
        confirmed = receipts if backlog else await asyncio.gather(*(self._confirm(receipt) for receipt in receipts))
        persistence_guaranteed = all(receipt.persistence_guaranteed for receipt in confirmed)
        if persistence_guaranteed != result.persistence_guaranteed:
            result = WindowResult(batch, result.algorithm_result, result.mode, result.completed_at_ms, persistence_guaranteed)
        algorithm_receipt = self._persist_algorithm(batch, result.algorithm_result, late=False)
        if not backlog:
            algorithm_receipt = await self._confirm(algorithm_receipt)
        if not algorithm_receipt.persistence_guaranteed and result.persistence_guaranteed:
            result = WindowResult(batch, result.algorithm_result, result.mode, result.completed_at_ms, False)
        async with self._lifecycle_lock:
            if batch.connection_session_id != self._connection_session_id:
                LOG.info("Window result invalidated while persistence completed: batchId=%s", batch.batch_id)
                self._discard_batch_frames(batch)
                return result
            order = self._batch_order[batch.batch_id]
            if self._last_published_order > order:
                self.diagnostics["superseded_results"] += 1
                self._discard_batch_frames(batch)
                return result
            self._last_published_order = order
            # Apply health only for the current, publishable window. Backlog
            # placeholders and late/old-session results cannot change it.
            if not backlog:
                if result.algorithm_result.valid and result.algorithm_result.metrics:
                    self._algorithm_state = AlgorithmState.READY
                elif set(result.algorithm_result.invalid_reasons) & {
                    "ALGORITHM_ERROR", "ALGORITHM_TIMEOUT", "ALGORITHM_NOT_READY",
                    "ALGORITHM_OUTPUT_INVALID", "ALGORITHM_INPUT_INVALID",
                }:
                    self._algorithm_state = AlgorithmState.ERROR
            self.data.transition(self.data.snapshot.data_state, algorithm_state=self._algorithm_state.value)
            self.diagnostics["publication_age_ms"] = max(0, self.clock_ms() - batch.window_end_ms)
            self.snapshots.replace(result)
            if self.data.snapshot.data_state in {DataState.READY, DataState.STREAMING, DataState.STALE}:
                self.data.produced(batch.window_end_ms, valid=result.valid)
            await self.northbound.publish(ApplicationEvent("window", result=result, algorithm_state=self._algorithm_state))
            self.data.transition(DataState.STREAMING, last_published_at_ms=self.clock_ms())
            self._schedule_stale(batch.window_end_ms)
            self.diagnostics["published"] += 1
            self._last_window_start_ms = batch.window_start_ms
            self._last_window_end_ms = batch.window_end_ms
        self._discard_batch_frames(batch)
        return result

    async def _persist_late(self, batch: ParsedSignalBatch, result: AlgorithmResult) -> None:
        await self._confirm(self._persist_algorithm(batch, result, late=True))
        LOG.warning(
            "Late algorithm result persisted without republishing: batchId=%s recordingSessionId=%s",
            batch.batch_id,
            batch.recording_session_id,
        )

    def _persist_algorithm(self, batch: ParsedSignalBatch, result: AlgorithmResult, *, late: bool) -> PersistenceReceipt:
        return self._persist(
            PersistenceRecord(
                1,
                batch.recording_session_id,
                "algorithm.late_result" if late else "algorithm.result",
                batch.window_end_ms,
                batch.batch_id,
                {
                    "batchId": batch.batch_id,
                    "algorithmVersion": result.algorithm_version,
                    "connectionSessionId": batch.connection_session_id,
                    "windowStartMs": batch.window_start_ms,
                    "windowEndMs": batch.window_end_ms,
                    "startedAtMs": result.started_at_ms,
                    "completedAtMs": result.completed_at_ms,
                    "metrics": result.metrics,
                    "valid": result.valid,
                    "invalidReasons": result.invalid_reasons,
                    "late": late,
                },
            )
        )

    def _persist(self, record: PersistenceRecord) -> PersistenceReceipt:
        if self.recording is None:
            self.diagnostics["persistence_gaps"] += 1
            return PersistenceReceipt(False, False, "recording_repository_not_bound")
        receipt = self.recording.try_append(record)
        if not receipt.persistence_guaranteed and receipt.reason != "write_pending":
            self.diagnostics["persistence_gaps"] += 1
        return receipt

    async def _confirm(self, receipt: PersistenceReceipt) -> PersistenceReceipt:
        if receipt.reason != "write_pending" or self.recording is None:
            return receipt
        confirm = getattr(self.recording, "confirm", None)
        if confirm is None:
            self.diagnostics["persistence_gaps"] += 1
            return PersistenceReceipt(False, False, "confirmation_unsupported")
        try:
            confirmed = await asyncio.wait_for(confirm(receipt), self.persistence_timeout_ms / 1000)
        except (TimeoutError, OSError):
            confirmed = PersistenceReceipt(False, False, "persistence_confirmation_timeout_or_error")
        if not confirmed.persistence_guaranteed:
            self.diagnostics["persistence_gaps"] += 1
        return confirmed

    def _discard_batch_frames(self, batch: ParsedSignalBatch) -> None:
        self._batch_order.pop(batch.batch_id, None)
        for frame_ref in batch.frame_refs:
            self._frames.pop(frame_ref, None)
            self._frame_receipts.pop(frame_ref, None)

    def _required_recording_id(self) -> str:
        if not self._recording_session_id:
            raise RuntimeError("Raw data arrived before a recording session was prepared")
        return self._recording_session_id

    def _schedule_flush(self) -> None:
        deadline = self.assembler.window_end_ms
        if deadline is None:
            return
        if self._flush_task and not self._flush_task.done() and self._flush_deadline_ms == deadline:
            return
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_deadline_ms = deadline
        self._flush_task = asyncio.create_task(self._flush_at(deadline))

    async def _flush_at(self, deadline_ms: int) -> None:
        try:
            await asyncio.sleep(max(0, deadline_ms - self.clock_ms()) / 1000)
            if self._flush_deadline_ms == deadline_ms:
                batch = self.assembler.flush()
                if batch is not None:
                    await self._submit_batch(batch)
        except asyncio.CancelledError:
            return
        finally:
            if self._flush_task is asyncio.current_task():
                self._flush_task = None
                self._flush_deadline_ms = None

    async def _cancel_flush(self) -> None:
        task, self._flush_task = self._flush_task, None
        self._flush_deadline_ms = None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _schedule_stale(self, produced_at_ms: int) -> None:
        if self._stale_task and not self._stale_task.done():
            self._stale_task.cancel()
        self._stale_task = asyncio.create_task(self._mark_stale(produced_at_ms))

    async def _mark_stale(self, produced_at_ms: int) -> None:
        try:
            await asyncio.sleep(self.stale_after_ms / 1000)
            if (
                self.data.snapshot.data_state == DataState.STREAMING
                and self.data.snapshot.last_produced_at_ms == produced_at_ms
            ):
                self.data.transition(DataState.STALE, recent_error="DATA_STALE", error_stage="acquisition")
                LOG.warning(
                    "Data stream became stale: connectionSessionId=%s lastProducedAtMs=%s staleAfterMs=%s",
                    self._connection_session_id,
                    produced_at_ms,
                    self.stale_after_ms,
                )
        except asyncio.CancelledError:
            return

    async def _cancel_stale(self) -> None:
        task, self._stale_task = self._stale_task, None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    def _signal_value(signal: ParsedSignal) -> dict[str, object]:
        return {
            "signalType": signal.signal_type,
            "samples": signal.samples,
            "sampleFormat": signal.sample_format,
            "sampleCount": signal.sample_count,
            "unit": signal.unit,
            "windowHint": signal.window_hint,
            "frameRefs": signal.frame_refs,
            "receivedAtMs": signal.received_at_ms,
            "valid": signal.valid,
            "invalidReasons": signal.invalid_reasons,
        }

    @staticmethod
    def _path_from_disconnected(target: ConnectionState) -> tuple[ConnectionState, ...]:
        paths = {
            ConnectionState.DISCOVERING: (ConnectionState.DISCOVERING,),
            ConnectionState.CONNECTING: (ConnectionState.DISCOVERING, ConnectionState.CONNECTING),
            ConnectionState.VALIDATING: (ConnectionState.DISCOVERING, ConnectionState.CONNECTING, ConnectionState.VALIDATING),
            ConnectionState.CONNECTED: (ConnectionState.DISCOVERING, ConnectionState.CONNECTING, ConnectionState.CONNECTED),
            ConnectionState.RECONNECTING: (ConnectionState.DISCOVERING, ConnectionState.RECONNECTING),
            ConnectionState.VALIDATION_FAILED: (
                ConnectionState.DISCOVERING,
                ConnectionState.CONNECTING,
                ConnectionState.VALIDATING,
                ConnectionState.VALIDATION_FAILED,
            ),
        }
        return paths[target]
