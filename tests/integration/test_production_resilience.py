from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
import sys
import unittest
from unittest.mock import patch
import zipfile

from neurobridge.adapters.northbound.protocol import project_window
from neurobridge.adapters.algorithms import AffectiveSdkAlgorithmEngine
from neurobridge.adapters.parsers import HeadbandBleParser
from neurobridge.adapters.storage.archive import ArchiveReplayReader
from neurobridge.adapters.storage.filesystem import SegmentedRecordingRepository
from neurobridge.adapters.northbound.protocol import envelope
from neurobridge.application.gateway import ClientSession, GatewayApplication
from neurobridge.bootstrap import build_container
from neurobridge.config import load
from neurobridge.domain.algorithm import AlgorithmResult, AlgorithmState
from neurobridge.domain.raw import RawChunk
from neurobridge.domain.status import ConnectionState, DataState
from neurobridge.ports.recording import PersistenceRecord
from neurobridge.profiles.resolver import RuntimePlatform


ROOT = Path(__file__).resolve().parents[2]


def frame(sequence=1):
    return b"\xaa\xaa\xaa\x1c" + sequence.to_bytes(2, "big") + bytes(range(18)) + b"\x48\xbb\xbb\xbb"


class Algorithm:
    available = True
    state = AlgorithmState.READY
    hold = None

    def __init__(self, _config):
        self.inputs = []

    async def initialize(self, session):
        self.session = session
        return self.state

    async def evaluate(self, value):
        if self.hold is not None:
            await self.hold.wait()
        self.inputs.append(value)
        return AlgorithmResult(value.batch_id, "test", 1, 2, {"attention": 42})

    async def close(self):
        pass

    stop = close


class ProductionResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        config = load(ROOT / "config/gateway.project.toml.example")
        # Real fsync/rename work needs the normal persistence and shutdown
        # budgets on shared CI runners. Short deadlines belong to stall tests.
        config = replace(config, recording=replace(config.recording, directory=Path(self.directory.name)),
                         pipeline=replace(config.pipeline, algorithm_queue_size=1, send_timeout_ms=30))
        with patch("neurobridge.bootstrap.container.AffectiveSdkAlgorithmEngine", Algorithm):
            self.container = build_container(config, RuntimePlatform("kylin", "x86_64"))
        self.gateway = self.container.gateway
        self.service = self.container.application
        self.source = self.container.source
        self.writes = []

        async def write(value):
            self.writes.append(value)
        self.source.control._write = write
        await self.gateway.start()
        self.addAsyncCleanup(self.cleanup_recording_repository, self.gateway.recording_repository)
        self.service.bind_recording(self.gateway.recording_repository)
        self.events = asyncio.create_task(self.container.device_adapter._consume_events())
        self.addAsyncCleanup(self.cleanup_runtime)

    async def cleanup_recording_repository(self, repository):
        # Run after runtime cleanup, even when it fails or stop() times out.
        # Keep the repository reference because gateway.stop() clears its own.
        await repository.close()
        await asyncio.to_thread(repository._worker.join, 5)
        self.assertFalse(repository._worker.is_alive(), "recording writer must exit before temporary directory cleanup")

    async def cleanup_runtime(self):
        self.events.cancel()
        await asyncio.gather(self.events, return_exceptions=True)
        for session in tuple(self.gateway.sessions):
            await self.gateway.close_session(session)
        await self.service.close()
        await self.gateway.stop()

    async def connect(self, *, existing=False):
        self.source._adapter._capture_started = existing
        await self.source._emit_state(ConnectionState.CONNECTED)
        await asyncio.wait_for(self.container.device_adapter._connected_seen.wait(), 1)
        return await self.container.device_adapter.device_ready()

    async def capture(self, count=1):
        now = int(time.time() * 1000)
        for i in range(count):
            await self.service.process(RawChunk("serial", "serial", frame(i + 1), now + i * 600,
                                               i, self.source.status().connection_session_id, f"trace-{i}"))

    async def test_composition_control_projection_archive_and_export(self):
        self.assertIs(type(self.gateway), GatewayApplication)
        self.assertIs(self.gateway.algorithm, self.service.algorithm)
        self.assertIs(self.service.control, self.source.control)
        self.assertIsNone(self.gateway.replay_reader)
        self.assertTrue(await self.connect())
        self.assertEqual(self.writes, [b"\xe1"])
        await self.capture()
        await self.service.flush()
        recording_id = self.gateway.store.recording_id
        await self.gateway.recording_repository.close_session(recording_id)
        archive = self.gateway.store
        results = list(archive.results(recording_id))
        self.assertEqual(len(results), 1)
        projected, _, _ = project_window(results[0])
        self.assertEqual(base64.b64decode(projected["eegRaw"]["bytesBase64"]), frame()[4:24])
        self.assertEqual(projected["eegRaw"]["packetBytes"], 20)
        self.assertEqual(projected["hrRaw"]["sampleFormat"], "bytes")
        self.assertFalse((archive._session_dir(recording_id) / "raw/eeg.jsonl").exists())
        rows = [r async for r in ArchiveReplayReader(archive).events(recording_id)]
        self.assertEqual(rows, [], "A BLE replay reader must not replay serial recordings from a shared directory")
        archive.stop()
        pdf = Path(self.directory.name) / "test.pdf"
        pdf.write_bytes(b"fixture pdf")
        archive._capture_package_pdf = pdf
        output = await asyncio.to_thread(archive.export, recording_id)
        with zipfile.ZipFile(output) as bundle:
            names = bundle.namelist()
            self.assertIn(f"{recording_id}/raw/eeg.jsonl", names)
            self.assertFalse(any("/parsed/" in n or "000001" in n or n.endswith(".partial") for n in names))
        await self.service.close()
        await self.source._stop_stream()
        self.assertEqual(self.writes, [b"\xe1", b"\xe0"])

    async def test_existing_stream_without_algorithm_is_adopted_without_e1(self):
        self.service.algorithm.state = AlgorithmState.UNAVAILABLE
        self.assertFalse(await self.connect(existing=True))
        self.assertEqual(self.writes, [])
        await self.capture()
        await self.service.flush()
        self.assertEqual(self.service.data.snapshot.data_state, DataState.STREAMING)
        await self.service.close()
        self.assertEqual(self.writes, [b"\xe0"])

    async def check_algorithm_failure_status(self, program):
        # The shutdown barrier must outlast the deliberately timed-out request.
        self.service.shutdown_timeout_ms = 1000
        engine = AffectiveSdkAlgorithmEngine(replace(self.gateway.config.algorithm,
            enabled=True, command=(sys.executable, "-u", "-c", program), request_timeout_ms=100))
        self.gateway.algorithm = self.service.algorithm = engine
        await self.connect()
        session, sent = ClientSession(), []
        async def send(message):
            sent.append(message)
        await self.gateway.subscribe(session, {"streams": ["status", "eeg.raw"]}, send)
        await self.capture()
        await self.service.flush()
        self.assertIsNone(engine._runner.process)
        self.assertEqual(self.gateway.status_result()["algorithmState"], "error")
        self.assertNotIn("eeg", self.gateway.status_result()["availableStreams"])
        self.assertEqual(self.service.data.snapshot.algorithm_state, "error")
        self.assertEqual(self.service._algorithm_state, AlgorithmState.ERROR)
        async with asyncio.timeout(1):
            while not any("status" in m["data"]["payload"] for m in sent) or not any("eegRaw" in m["data"]["payload"] for m in sent):
                await asyncio.sleep(0.005)
        statuses = [m["data"]["payload"]["status"]["algorithmState"] for m in sent if "status" in m["data"]["payload"]]
        self.assertEqual(statuses, ["error"])
        self.assertTrue(next(m for m in sent if "eegRaw" in m["data"]["payload"])["data"]["valid"])
        # Further windows keep the raw path alive without restoring false ready.
        await self.capture()
        await self.service.flush()
        self.assertEqual(self.gateway.status_result()["algorithmState"], "error")
        await self.source._emit_state(ConnectionState.RECONNECTING)
        async with asyncio.timeout(1):
            while self.gateway.status["connectionState"] != "disconnected":
                await asyncio.sleep(0.005)
        await self.connect()
        self.assertEqual(self.gateway.status_result()["algorithmState"], "ready")
        self.assertEqual(self.service.data.snapshot.algorithm_state, "ready")

    async def test_bridge_error_updates_status_and_preserves_raw_capture(self):
        await self.check_algorithm_failure_status('import sys; sys.stdin.readline(); print(\'{"bridgeError":"synthetic failure"}\', flush=True)')

    async def test_bridge_timeout_updates_status_and_preserves_raw_capture(self):
        await self.check_algorithm_failure_status('import sys, time; sys.stdin.readline(); time.sleep(60)')

    async def test_bridge_exit_updates_status_and_preserves_raw_capture(self):
        await self.check_algorithm_failure_status('import sys; sys.stdin.readline(); sys.exit(3)')

    async def test_bridge_invalid_output_updates_status_and_preserves_raw_capture(self):
        await self.check_algorithm_failure_status('import sys; sys.stdin.readline(); print("{}", flush=True)')

    async def test_ack_algorithm_initialization_exception_is_error_without_e1(self):
        async def fail(_session):
            raise RuntimeError("injected initialization failure")
        self.service.algorithm.initialize = fail
        self.assertFalse(await self.connect())
        self.assertEqual(self.service.data.snapshot.data_state, DataState.ERROR)
        self.assertEqual(self.writes, [])

    async def test_algorithm_backlog_does_not_block_acquisition_or_disconnect(self):
        self.service.shutdown_timeout_ms = 100
        await self.connect()
        self.service.algorithm.hold = asyncio.Event()
        await asyncio.wait_for(self.capture(8), 0.2)
        self.assertEqual(self.service.diagnostics["frames"], 8)
        self.assertGreater(self.service.diagnostics["algorithm_backlog"], 0)
        self.assertLessEqual(self.service.metrics()["algorithm_queue_depth"], 1)
        await self.source._emit_state(ConnectionState.RECONNECTING)
        async with asyncio.timeout(1):
            while self.service.data.snapshot.data_state != DataState.UNAVAILABLE:
                await asyncio.sleep(0.005)
        self.assertFalse(self.events.done(), "disconnect must not crash on nonexistent parser")

    async def test_status_and_data_have_separate_pending_slots_and_send_deadline(self):
        await self.connect()
        session = ClientSession()
        sent = []
        blocked = asyncio.Event()
        async def send(message):
            await blocked.wait()
            sent.append(message)
        response = await self.gateway.subscribe(session, {"streams": ["status", "eeg.raw"]}, send)
        sub = session.subscriptions[response["subscriptionId"]]
        def offer(payload):
            self.gateway._queue_live_message(session, sub, envelope(200, self.gateway.event_data("data", sub.id, 1, "live", True, payload)))
        offer({"status": {"connectionState": "connected"}})
        offer({"eegRaw": {"bytesBase64": "AA=="}})
        self.assertEqual(self.gateway.fanout.pending_count, 2)
        await asyncio.sleep(0.08)
        self.assertNotIn(sub.id, session.subscriptions)
        self.assertTrue(sub.replay_delivery_task.done())
        self.assertEqual(self.gateway.fanout.pending_count, 0)
        self.assertEqual(self.gateway.delivery_metrics["failed"], 1)

    async def test_periodic_metrics_keep_internal_storage_off_wire(self):
        await self.connect()
        with self.assertLogs("neurobridge.bootstrap.container", "INFO") as logs:
            task = asyncio.create_task(self.container.device_adapter._observe())
            await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        line = next(line for line in logs.output if "Runtime metrics:" in line)
        metrics = json.loads(line.split("Runtime metrics: ", 1)[1])
        self.assertIn("process", metrics)
        self.assertIn("algorithm_queue_depth", metrics["pipeline"])
        self.assertNotIn("storageState", self.gateway.status_result())

    async def test_long_run_metrics_measure_capture_and_idle_without_payloads(self):
        await self.connect()
        adapter = self.container.device_adapter
        self.assertIsNone(self.service.metrics()["last_frame_age_ms"])
        with self.assertLogs("neurobridge.bootstrap.container", "INFO") as logs:
            adapter._log_metrics()
            await self.capture(2)
            await self.service.flush()
            adapter._log_metrics(event_loop_lag_ms=125)
            adapter._log_metrics()
        samples = [json.loads(line.split("Runtime metrics: ", 1)[1]) for line in logs.output if "Runtime metrics: " in line]
        active, idle = samples[-2:]
        self.assertEqual(active["progress"]["delta"]["frames"], 2)
        self.assertGreater(active["progress"]["delta"]["algorithm_valid_results"], 0)
        self.assertEqual(active["progress"]["delta"]["algorithm_invalid_results"], 0)
        self.assertEqual(idle["progress"]["delta"]["frames"], 0)
        self.assertEqual(active["eventLoopLagMs"], 125)
        self.assertIsNotNone(active["lastStorageSuccessAtMs"])
        self.assertIsNotNone(active["pipeline"]["last_window_start_ms"])
        before = self.service.metrics()["last_frame_age_ms"]
        with patch("neurobridge.application.service.time.time", return_value=0):
            self.assertGreaterEqual(self.service.metrics()["last_frame_age_ms"], before)
        # Synthetic algorithm values and device bytes belong only in recordings.
        self.assertNotIn("attention", json.dumps(samples))
        self.assertNotIn("bytesBase64", json.dumps(samples))
        self.assertNotIn(base64.b64encode(frame()).decode(), json.dumps(samples))

    async def test_metrics_failure_is_isolated_and_shutdown_emits_final_counters(self):
        await self.connect()
        adapter = self.container.device_adapter
        with patch.object(adapter._metrics, "sample", side_effect=OSError("synthetic counter failure")):
            with self.assertLogs("neurobridge.bootstrap.container", "ERROR"):
                adapter._log_metrics()
        await self.capture()
        with self.assertLogs("neurobridge.bootstrap.container", "INFO") as logs:
            await adapter.stop()
        final = json.loads(next(line.split("Runtime metrics: ", 1)[1] for line in logs.output if "Runtime metrics: " in line))
        self.assertEqual(final["phase"], "acquisition_shutdown")
        self.assertEqual(final["pipeline"]["frames"], 1)
        self.assertGreaterEqual(final["pipeline"]["published"], 1)

    async def test_algorithm_timeout_is_counted_without_requiring_client_subscription(self):
        await self.connect()
        self.service.aggregator.timeout_ms = 10
        self.service.algorithm.hold = asyncio.Event()
        await self.capture()
        await self.service.flush()
        metrics = self.service.metrics()
        self.assertEqual(metrics["algorithm_evaluations"], 1)
        self.assertEqual(metrics["algorithm_timeout_results"], 1)
        self.assertEqual(metrics["algorithm_invalid_results"], 1)
        self.assertEqual(metrics["algorithm_valid_results"], 0)
        self.assertGreaterEqual(metrics["algorithm_max_duration_ms"], 1)

    async def test_known_source_gap_discards_partial_frame_and_marks_window(self):
        await self.connect()
        session = self.source.status().connection_session_id
        now = int(time.time() * 1000)
        await self.service.process(RawChunk("serial", "serial", frame(1)[:10], now, 1, session, "partial"))
        outcome = await self.service.process(RawChunk("serial", "serial", frame(2), now + 1, 2, session, "after-gap", 18))
        self.assertEqual([f.raw_bytes for f in outcome.frames], [frame(2)])
        self.assertFalse(outcome.signals[0].valid)
        self.assertIn("SOURCE_QUEUE_GAP", outcome.signals[0].invalid_reasons)
        self.assertEqual(self.service.metrics()["source_gap_bytes"], 18)

    async def test_reconnect_clears_old_snapshot_and_freshness_uses_monotonic_time(self):
        await self.connect()
        await self.capture()
        await self.service.flush()
        session = ClientSession()
        self.assertTrue(self.gateway.get_latest(session, {"streams": ["eeg"]})["valid"])
        self.gateway._last_live_update = time.monotonic() - 10
        self.assertFalse(self.gateway.get_latest(session, {"streams": ["eeg"]})["valid"])
        await self.source._emit_state(ConnectionState.RECONNECTING)
        async with asyncio.timeout(1):
            while self.gateway.live:
                await asyncio.sleep(0.005)
        await self.connect()
        latest = self.gateway.get_latest(session, {"streams": ["eeg"]})
        self.assertFalse(latest["valid"])
        self.assertEqual(latest["payload"], {})

    async def test_wall_clock_rollback_does_not_suppress_newer_capture(self):
        await self.connect()
        session = self.source.status().connection_session_id
        now = int(time.time() * 1000)
        await self.service.process(RawChunk("serial", "serial", frame(1), now, 1, session, "before"))
        await self.service.flush()
        first = self.service.snapshots.get(frozenset({"eeg"})).version
        await self.service.process(RawChunk("serial", "serial", frame(2), now - 10000, 2, session, "after"))
        await self.service.flush()
        second = self.service.snapshots.get(frozenset({"eeg"}))
        self.assertGreater(second.version.value, first.value)
        self.assertLess(second.result.batch.window_end_ms, now)

    async def test_headband_segmented_replay_reads_saved_results_without_sdk(self):
        await self.check_headband_segmented_replay()

    async def test_headband_replay_waits_for_slow_recording_finalization(self):
        repository = self.gateway.recording_repository
        original = repository._close_session_sync

        def slow_close(recording_id):
            # Exceed the former 100 ms fixture deadline deterministically.
            time.sleep(0.15)
            original(recording_id)

        with patch.object(repository, "_close_session_sync", side_effect=slow_close):
            await self.check_headband_segmented_replay()

    async def check_headband_segmented_replay(self):
        self.service.parser = HeadbandBleParser()
        self.service.device_protocol = "headband_ble"
        await self.connect()
        session = self.source.status().connection_session_id
        now = int(time.time() * 1000)
        await self.service.process(RawChunk("bluetooth", "ff31", bytes(range(20)), now, 1, session, "ble-frame"))
        await self.service.flush()
        recording_id = self.gateway.store.recording_id
        await self.gateway.recording_repository.close_session(recording_id)
        session_path = Path(self.directory.name) / "sessions" / recording_id
        manifest = json.loads((session_path / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("endedAtMs", manifest, "replay requires completed recording finalization")
        self.assertEqual(list(session_path.rglob("*.partial")), [])
        self.gateway.store.stop()
        count = len(self.service.algorithm.inputs)
        reader = ArchiveReplayReader(self.gateway.store)
        selection = await reader.inspect(None)
        self.assertEqual(selection[0], recording_id)
        rows = [row async for row in reader.events(recording_id)]
        self.assertEqual(rows[0].payload["algorithm"], {"attention": 42})
        self.assertEqual(base64.b64decode(rows[0].payload["eegRaw"]["bytesBase64"]), bytes(range(20)))
        self.assertEqual(len(self.service.algorithm.inputs), count)


    async def test_optional_trace_is_bounded_and_separate_from_complete_frames(self):
        self.service.transport_trace_enabled = True
        self.service.transport_trace_max_bytes = 28
        await self.connect()
        await self.capture(2)
        await self.service.flush()
        recording_id = self.gateway.store.recording_id
        await self.gateway.recording_repository.close_session(recording_id)
        paths = list((Path(self.directory.name)/'sessions'/recording_id/'raw').glob('*.jsonl'))
        rows = [json.loads(line) for path in paths for line in path.read_text().splitlines()]
        trace = [r for r in rows if r['recordType']=='raw.transport_chunk']
        frames = [r for r in rows if r['recordType']=='raw.device_frame']
        self.assertEqual(len(trace), 1)
        self.assertEqual(len(frames), 2)
        self.assertEqual(base64.b64decode(trace[0]['payload']['rawBytes']['bytesBase64']), frame())
        self.assertEqual(self.service.diagnostics['trace_omitted_bytes'], 28)


    async def test_unavailable_recording_path_does_not_prevent_gateway_start(self):
        bad_root = Path(self.directory.name) / "not-a-directory"
        bad_root.write_text("fixture")
        cfg = replace(self.gateway.config, recording=replace(self.gateway.config.recording, directory=bad_root))
        with patch("neurobridge.bootstrap.container.AffectiveSdkAlgorithmEngine", Algorithm):
            container = build_container(cfg, RuntimePlatform("kylin", "x86_64"))
        await container.gateway.start()
        try:
            self.assertEqual(container.gateway.status_result()["mode"], "live")
            self.assertEqual(container.gateway.recording_repository.storage_status().state.value, "error")
            self.assertIsNone(container.gateway.replay_reader)
        finally:
            await container.gateway.stop()


    async def test_delivery_metrics_track_success_failure_and_overwrites_per_stream(self):
        await self.connect()
        session = ClientSession()
        async def send(_): pass
        response = await self.gateway.subscribe(session, {"streams": ["eeg.raw"]}, send)
        subscription = session.subscriptions[response["subscriptionId"]]
        await self.gateway._send_subscription(subscription, {}, streams={"eeg.raw"})
        self.assertEqual(self.gateway.stream_metrics["eeg.raw"]["sent"], 1)
        self.assertIsNotNone(self.gateway.stream_metrics["eeg.raw"]["lastSuccessfulAtMs"])
        self.assertEqual(self.gateway.stream_metrics["hr.raw"]["sent"], 0)
        async def fail(_): raise ConnectionError("synthetic send failure")
        subscription.send = fail
        with self.assertRaises(ConnectionError):
            await self.gateway._send_subscription(subscription, {}, streams={"eeg.raw"})
        self.assertEqual(self.gateway.stream_metrics["eeg.raw"]["failed"], 1)
        self.gateway.fanout.offer(subscription.id, frozenset({"eeg.raw"}), 1)
        self.gateway.fanout.offer(subscription.id, frozenset({"eeg.raw"}), 2)
        self.assertEqual(self.gateway.fanout.overwrites_by_stream["eeg.raw"], 1)
        await self.gateway.close_session(session)


class StorageStallTests(unittest.IsolatedAsyncioTestCase):
    async def test_disk_stall_never_holds_status_acquisition_or_executor_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SegmentedRecordingRepository(directory, queue_size=1, shutdown_timeout_ms=30)
            entered, release = threading.Event(), threading.Event()
            original = repo._open_segment
            def stalled(*args, **kwargs):
                entered.set()
                release.wait(2)
                return original(*args, **kwargs)
            repo._open_segment = stalled
            receipt = repo.try_append(PersistenceRecord(1, "rec-1", "raw.device_frame", 1, "f1", {}))
            try:
                async with asyncio.timeout(1):
                    while not entered.is_set():
                        await asyncio.sleep(0.005)
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(repo.confirm(receipt), 0.01)
                repo.storage_status()
                repo.try_append(PersistenceRecord(1, "rec-1", "raw.device_frame", 2, "f2", {}))
                rejected = repo.try_append(PersistenceRecord(1, "rec-1", "raw.device_frame", 3, "f3", {}))
                self.assertFalse(rejected.accepted)
                await asyncio.wait_for(repo.close(), 0.2)
                self.assertEqual(repo.storage_status().details["lastFailureReason"], "writer_shutdown_timeout")
            finally:
                release.set()
                async with asyncio.timeout(1):
                    while repo._worker.is_alive():
                        await asyncio.sleep(0.005)
