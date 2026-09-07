from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from neurobridge.adapters.parsers import HeadbandBleParser, HeadsetRev181Parser
from neurobridge.adapters.storage import SegmentedRecordingRepository
from neurobridge.application.processing import AlgorithmInputMapper, WindowResultAggregator
from neurobridge.application.snapshots import InMemoryLatestSnapshotStore
from neurobridge.application.status import ConnectionStateMachine, DataStateMachine, StorageHealthTracker
from neurobridge.application.subscriptions import SubscriptionFanout
from neurobridge.application.windowing import SignalWindowAssembler
from neurobridge.domain.algorithm import AlgorithmInput, AlgorithmResult
from neurobridge.domain.raw import RawChunk
from neurobridge.domain.result import WindowResult
from neurobridge.domain.status import ConnectionState, DataState, StorageState
from neurobridge.ports.raw_parser import FlushReason
from neurobridge.ports.recording import PersistenceRecord


def chunk(data: bytes, *, timestamp: int = 600, channel: str = "serial") -> RawChunk:
    return RawChunk("serial", channel, data, timestamp, timestamp * 1_000_000, "conn-1", f"trace-{timestamp}-{len(data)}")


def frame(sequence: int = 1) -> bytes:
    return b"\xaa\xaa\xaa\x1c" + sequence.to_bytes(2, "big") + bytes(range(18)) + b"\x48" + b"\xbb\xbb\xbb"


class HeadsetParserTests(unittest.TestCase):
    def test_split_and_sticky_frames_preserve_original_bytes(self) -> None:
        parser = HeadsetRev181Parser()
        first = frame(9)
        second = frame(10)
        partial = parser.feed(chunk(first[:12], timestamp=100))
        self.assertEqual(partial.buffered_bytes, 12)
        outcome = parser.feed(chunk(first[12:] + second, timestamp=101))
        self.assertEqual([item.raw_bytes for item in outcome.frames], [first, second])
        self.assertEqual(outcome.frames[0].sequence, 9)
        eeg = [item for item in outcome.signals if item.signal_type == "eeg"]
        self.assertEqual(len(eeg[0].samples), 18)
        self.assertEqual(eeg[0].samples, first[6:24])

    def test_noise_invalid_tail_and_flush_become_diagnostics(self) -> None:
        parser = HeadsetRev181Parser()
        invalid = frame()[:-1] + b"\x00"
        outcome = parser.feed(chunk(b"noise" + invalid))
        self.assertGreater(outcome.discarded_bytes, 0)
        self.assertIn("noise", {item.kind for item in outcome.diagnostics})
        parser.feed(chunk(frame()[:9], timestamp=601))
        flushed = parser.flush(FlushReason.DISCONNECTED)
        self.assertEqual(flushed.buffered_bytes, 0)
        self.assertTrue(flushed.diagnostics)

    def test_sequence_gap_is_diagnostic_but_does_not_rewrite_samples(self) -> None:
        parser = HeadsetRev181Parser()
        parser.feed(chunk(frame(1), timestamp=1))
        outcome = parser.feed(chunk(frame(3), timestamp=2))
        self.assertIn("sequence_gap", {item.kind for item in outcome.diagnostics})
        self.assertTrue(all(item.valid for item in outcome.signals))


class HeadbandParserTests(unittest.TestCase):
    def test_invalid_characteristic_length_is_explicit(self) -> None:
        parser = HeadbandBleParser()
        outcome = parser.feed(chunk(b"short", channel="ff31"))
        self.assertFalse(outcome.signals[0].valid)
        self.assertEqual(outcome.signals[0].invalid_reasons, ("EEG_PACKET_LENGTH_INVALID",))


class WindowAndSnapshotTests(unittest.TestCase):
    def _result(self) -> WindowResult:
        parser = HeadsetRev181Parser()
        parsed = parser.feed(chunk(frame(5), timestamp=601))
        assembler = SignalWindowAssembler(600)
        for signal in parsed.signals:
            self.assertEqual(assembler.append(signal, device_protocol="headset_rev181", connection_session_id="conn-1", recording_session_id="rec-1"), ())
        batch = assembler.flush()
        assert batch is not None
        algorithm = AlgorithmResult(batch.batch_id, "test", 1, 2, {"attention": 50})
        return WindowResult(batch, algorithm, "live", 2, True)

    def test_algorithm_projection_keeps_sequence_and_eeg_sdk_bytes(self) -> None:
        parser = HeadsetRev181Parser()
        parsed = parser.feed(chunk(frame(0x1234), timestamp=601))
        assembler = SignalWindowAssembler(600)
        for signal in parsed.signals:
            assembler.append(signal, device_protocol="headset_rev181", connection_session_id="conn-1", recording_session_id="rec-1")
        batch = assembler.flush()
        assert batch is not None
        mapped = AlgorithmInputMapper().map(batch, parsed.frames)
        import base64

        self.assertEqual(base64.b64decode(mapped.payload["eegRawBase64"]), frame(0x1234)[4:24])
        self.assertEqual(mapped.payload["eegPacketCount"], 1)
        self.assertEqual(mapped.payload["hrPacketCount"], 1)

    def test_snapshot_replace_is_one_batch_transaction(self) -> None:
        store = InMemoryLatestSnapshotStore()
        result = self._result()
        version = store.replace(result)
        read = store.get(frozenset({"eeg", "hr"}))
        self.assertEqual(version.batch_id, result.batch.batch_id)
        self.assertIs(read.result, result)

    def test_fanout_keeps_only_latest_value_per_stream(self) -> None:
        async def scenario() -> None:
            fanout = SubscriptionFanout()
            fanout.subscribe("client", frozenset({"eeg", "hr"}))
            result = self._result()
            fanout.publish(result)
            fanout.publish(result)
            values = await asyncio.wait_for(fanout.next("client"), 0.1)
            self.assertEqual(set(values), {"eeg", "hr"})
            self.assertEqual(fanout.snapshot_overwrite_count, 2)

        asyncio.run(scenario())

    def test_algorithm_timeout_closes_snapshot_slot_and_late_result_is_not_returned(self) -> None:
        async def scenario() -> None:
            batch = self._result().batch
            late: list[AlgorithmResult] = []

            class SlowEngine:
                async def evaluate(self, value: AlgorithmInput) -> AlgorithmResult:
                    await asyncio.sleep(0.02)
                    return AlgorithmResult(value.batch_id, "late", 1, 2, {"attention": 99})

            aggregator = WindowResultAggregator(SlowEngine(), timeout_ms=1)

            async def persist_late(result: AlgorithmResult) -> None:
                late.append(result)

            result = await aggregator.evaluate(
                batch,
                AlgorithmInput(batch.batch_id, {}, "1"),
                persistence_guaranteed=True,
                on_late_result=persist_late,
            )
            self.assertFalse(result.valid)
            self.assertEqual(result.algorithm_result.invalid_reasons, ("ALGORITHM_TIMEOUT",))
            await asyncio.sleep(0.03)
            self.assertEqual(len(late), 1)
            self.assertEqual(aggregator.late_result_count, 1)

        asyncio.run(scenario())


class StateMachineTests(unittest.TestCase):
    def test_connection_and_data_states_are_independent(self) -> None:
        connection = ConnectionStateMachine()
        data = DataStateMachine()
        connection.transition(ConnectionState.DISCOVERING, 1)
        connection.transition(ConnectionState.CONNECTING, 2)
        connection.transition(ConnectionState.VALIDATING, 3)
        event = connection.transition(ConnectionState.CONNECTED, 4, connection_session_id="conn-1")
        self.assertEqual(data.on_connection(event).data_state, DataState.PREPARING)
        self.assertEqual(data.transition(DataState.READY).data_state, DataState.READY)
        self.assertEqual(data.produced(10, valid=True).data_state, DataState.STREAMING)

    def test_storage_recovery_requires_write_and_three_healthy_checks(self) -> None:
        tracker = StorageHealthTracker(100, 20, recovery_margin_bytes=10)
        self.assertEqual(tracker.observe(10), StorageState.FULL)
        self.assertEqual(tracker.observe(120), StorageState.FULL)
        self.assertEqual(tracker.observe(120, write_succeeded=True), StorageState.FULL)
        self.assertEqual(tracker.observe(120), StorageState.FULL)
        self.assertEqual(tracker.observe(120), StorageState.OK)


class SegmentedRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_are_segmented_and_manifested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SegmentedRecordingRepository(
                directory,
                queue_size=4,
                segment_max_bytes=1024,
                warning_threshold_bytes=2,
                critical_threshold_bytes=1,
            )
            receipt = repository.try_append(PersistenceRecord(1, "rec-test", "raw.device_frame", 10, "frame-1", {"rawBytes": frame()}))
            self.assertTrue(receipt.accepted)
            await repository.close_session("rec-test")
            manifest = Path(directory) / "sessions/rec-test/manifest.json"
            self.assertTrue(manifest.is_file())
            self.assertTrue(list((manifest.parent / "raw").glob("*.jsonl")))
            self.assertFalse(list((manifest.parent / "raw").glob("*.partial")))
            await repository.close()

    async def test_cleanup_is_off_by_default_and_only_removes_eligible_completed_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            free = shutil.disk_usage(root).free
            for name, retain in (("rec-old", False), ("rec-retained", True)):
                session = root / "sessions" / name
                session.mkdir(parents=True)
                (session / "manifest.json").write_text(
                    json.dumps({"endedAtMs": 1, "retain": retain}),
                    encoding="utf-8",
                )
            disabled = SegmentedRecordingRepository(root, warning_threshold_bytes=free + 2, critical_threshold_bytes=free + 1)
            self.assertEqual(disabled.cleanup_completed_sessions(), ())
            await disabled.close()
            enabled = SegmentedRecordingRepository(
                root,
                warning_threshold_bytes=free + 2,
                critical_threshold_bytes=free + 1,
                auto_cleanup_enabled=True,
            )
            removed = enabled.cleanup_completed_sessions()
            self.assertEqual(removed, ("rec-old",))
            self.assertTrue((root / "sessions/rec-retained").is_dir())
            await enabled.close()

    async def test_partial_segment_is_truncated_and_marked_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "sessions/rec-recover/raw"
            raw.mkdir(parents=True)
            row = {"capturedAtMs": 10, "value": 1}
            partial = raw / "000001.jsonl.partial"
            partial.write_bytes((json.dumps(row) + "\n" + "{broken").encode())
            repository = SegmentedRecordingRepository(
                root,
                warning_threshold_bytes=2,
                critical_threshold_bytes=1,
            )
            recovered = raw / "000001.jsonl"
            self.assertEqual(recovered.read_text(encoding="utf-8"), json.dumps(row) + "\n")
            manifest = json.loads((raw.parent / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["segments"][0]["status"], "recovered")
            await repository.close()
