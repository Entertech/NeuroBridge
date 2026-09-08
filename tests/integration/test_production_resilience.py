from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from neurobridge.adapters.northbound.protocol import project_window
from neurobridge.adapters.parsers import HeadbandBleParser
from neurobridge.adapters.storage.archive import ArchiveReplayReader
from neurobridge.adapters.storage.filesystem import SegmentedRecordingRepository
from neurobridge.application.gateway import ClientSession, GatewayApplication, envelope
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
        config = replace(config, recording=replace(config.recording, directory=Path(self.directory.name)),
                         pipeline=replace(config.pipeline, shutdown_timeout_ms=100, algorithm_queue_size=1,
                                          send_timeout_ms=30, persistence_timeout_ms=30))
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
        self.service.bind_recording(self.gateway.recording_repository)
        self.events = asyncio.create_task(self.container.device_adapter._consume_events())
        self.addAsyncCleanup(self.cleanup_runtime)

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

    async def test_ack_algorithm_initialization_exception_is_error_without_e1(self):
        async def fail(_session):
            raise RuntimeError("injected initialization failure")
        self.service.algorithm.initialize = fail
        self.assertFalse(await self.connect())
        self.assertEqual(self.service.data.snapshot.data_state, DataState.ERROR)
        self.assertEqual(self.writes, [])

    async def test_algorithm_backlog_does_not_block_acquisition_or_disconnect(self):
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
        self.service.parser = HeadbandBleParser()
        self.service.device_protocol = "headband_ble"
        await self.connect()
        session = self.source.status().connection_session_id
        now = int(time.time() * 1000)
        await self.service.process(RawChunk("bluetooth", "ff31", bytes(range(20)), now, 1, session, "ble-frame"))
        await self.service.flush()
        recording_id = self.gateway.store.recording_id
        await self.gateway.recording_repository.close_session(recording_id)
        self.gateway.store.stop()
        count = len(self.service.algorithm.inputs)
        reader = ArchiveReplayReader(self.gateway.store)
        selection = await reader.inspect(None)
        self.assertEqual(selection[0], recording_id)
        rows = [row async for row in reader.events(recording_id)]
        self.assertEqual(rows[0].payload["algorithm"], {"attention": 42})
        self.assertEqual(base64.b64decode(rows[0].payload["eegRaw"]["bytesBase64"]), bytes(range(20)))
        self.assertEqual(len(self.service.algorithm.inputs), count)


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
