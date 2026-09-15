from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from neurobridge.adapters.northbound.publisher import CollectingNorthboundSink, GatewayNorthboundSink
from neurobridge.adapters.parsers import HeadbandBleParser, HeadsetRev181Parser
from neurobridge.adapters.storage import SegmentedRecordingRepository
from neurobridge.application.service import ApplicationService
from neurobridge.application.snapshots import InMemoryLatestSnapshotStore
from neurobridge.business.gateway import ClientSession, Gateway
from neurobridge.bootstrap.container import _ApplicationPipelineAdapter
from neurobridge.config import load
from neurobridge.domain.algorithm import AlgorithmResult, AlgorithmState
from neurobridge.domain.raw import RawChunk
from neurobridge.domain.status import ConnectionState, DataState, DeviceConnectionEvent
from neurobridge.ports.raw_source import SourceStatus


def headset_frame(sequence: int = 1) -> bytes:
    return b"\xaa\xaa\xaa\x1c" + sequence.to_bytes(2, "big") + bytes(range(18)) + b"\x48\xbb\xbb\xbb"


class FakeAlgorithm:
    def __init__(self) -> None:
        self.inputs = []
        self.closed = False

    async def initialize(self, session) -> AlgorithmState:
        self.session = session
        return AlgorithmState.READY

    async def evaluate(self, value) -> AlgorithmResult:
        self.inputs.append(value)
        return AlgorithmResult(value.batch_id, "fake-1", 610, 620, {"attention": 42})

    async def close(self) -> None:
        self.closed = True


class FakeUnavailableAlgorithm:
    async def initialize(self, session) -> AlgorithmState:
        self.session = session
        return AlgorithmState.UNAVAILABLE

    async def evaluate(self, value) -> AlgorithmResult:
        raise AssertionError("Unavailable algorithm must not be evaluated")

    async def close(self) -> None:
        return None


class ControlledAlgorithm(FakeAlgorithm):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def evaluate(self, value) -> AlgorithmResult:
        self.started.set()
        await self.release.wait()
        return await super().evaluate(value)


class SlowAlgorithm(FakeAlgorithm):
    async def evaluate(self, value) -> AlgorithmResult:
        await asyncio.sleep(0.02)
        return await super().evaluate(value)


class ApplicationPipelineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_serial_stream_opens_raw_pipeline_when_algorithm_is_unavailable(self) -> None:
        class Source:
            existing_stream = True

            def status(self) -> SourceStatus:
                return SourceStatus(ConnectionState.CONNECTED, "conn-existing")

        class Application:
            requires_algorithm_to_start = True

            async def prepare_session(self, connection_id, recording_id, *, existing_stream=False):
                self.prepared = (connection_id, recording_id, existing_stream)
                return AlgorithmState.UNAVAILABLE

        class GatewayStub:
            store = SimpleNamespace(recording_id="rec-existing")

            async def update_status(self, _name, _value) -> None:
                return None

        application = Application()
        bridge = _ApplicationPipelineAdapter(
            Source(),
            application,
            GatewayStub(),
            SimpleNamespace(transport="serial"),
        )
        bridge._connected_seen.set()

        algorithm_ready = await bridge.device_ready()

        self.assertFalse(algorithm_ready)
        self.assertTrue(bridge._ready.is_set())
        self.assertEqual(application.prepared, ("conn-existing", "rec-existing", True))

    async def test_raw_frame_to_algorithm_snapshot_and_storage_keeps_correlations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SegmentedRecordingRepository(
                directory,
                warning_threshold_bytes=2,
                critical_threshold_bytes=1,
                fsync_interval_records=1,
            )
            snapshots = InMemoryLatestSnapshotStore()
            sink = CollectingNorthboundSink()
            algorithm = FakeAlgorithm()
            service = ApplicationService(
                device_protocol="headset_rev181",
                parser=HeadsetRev181Parser(),
                algorithm=algorithm,
                snapshots=snapshots,
                northbound=sink,
                recording=repository,
            )
            for state in (ConnectionState.DISCOVERING, ConnectionState.CONNECTING):
                await service.on_connection(DeviceConnectionEvent(state, 1))
            await service.on_connection(DeviceConnectionEvent(ConnectionState.CONNECTED, 2, "conn-1"))
            self.assertEqual(
                await service.prepare_session("conn-1", "rec-1"),
                AlgorithmState.READY,
            )
            frame = headset_frame(0x1234)
            await service.process(RawChunk("serial", "serial", frame, 601, 1, "conn-1", "trace-1"))
            await service.flush()
            self.assertEqual(len(sink.events), 1)
            result = sink.events[0].result
            assert result is not None
            self.assertEqual(result.batch.connection_session_id, "conn-1")
            self.assertEqual(result.batch.frame_refs, tuple(result.batch.signals[0].frame_refs))
            self.assertEqual(result.algorithm_result.metrics["attention"], 42)
            self.assertEqual(base64.b64decode(algorithm.inputs[0].payload["eegRawBase64"]), frame[4:24])
            self.assertEqual(snapshots.get(frozenset({"eeg"})).version.batch_id, result.batch.batch_id)
            await repository.close_session("rec-1")
            await service.close()
            await repository.close()
            rows = []
            for path in (Path(directory) / "sessions/rec-1").glob("*/*.jsonl"):
                rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
            raw = next(row for row in rows if row["recordType"] == "raw.device_frame")
            parsed = next(row for row in rows if row["recordType"] == "parsed.signal_batch")
            algorithm_row = next(row for row in rows if row["recordType"] == "algorithm.result")
            self.assertEqual(raw["correlationId"], parsed["payload"]["frameRefs"][0])
            self.assertEqual(parsed["correlationId"], algorithm_row["correlationId"])

    async def test_stale_connection_chunk_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SegmentedRecordingRepository(directory, warning_threshold_bytes=2, critical_threshold_bytes=1)
            sink = CollectingNorthboundSink()
            service = ApplicationService(
                device_protocol="headset_rev181",
                parser=HeadsetRev181Parser(),
                algorithm=FakeAlgorithm(),
                snapshots=InMemoryLatestSnapshotStore(),
                northbound=sink,
                recording=repository,
            )
            await service.prepare_session("conn-current", "rec-1")
            outcome = await service.process(RawChunk("serial", "serial", headset_frame(), 1, 1, "conn-old", "trace-old"))
            self.assertEqual(outcome.frames, ())
            self.assertEqual(sink.events, [])
            await service.close()
            await repository.close()

    async def test_disconnect_during_algorithm_evaluation_discards_old_session_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SegmentedRecordingRepository(directory, warning_threshold_bytes=2, critical_threshold_bytes=1)
            sink = CollectingNorthboundSink()
            algorithm = ControlledAlgorithm()
            service = ApplicationService(
                device_protocol="headset_rev181",
                parser=HeadsetRev181Parser(),
                algorithm=algorithm,
                snapshots=InMemoryLatestSnapshotStore(),
                northbound=sink,
                recording=repository,
            )
            for state in (ConnectionState.DISCOVERING, ConnectionState.CONNECTING):
                await service.on_connection(DeviceConnectionEvent(state, 1))
            await service.on_connection(DeviceConnectionEvent(ConnectionState.CONNECTED, 2, "conn-1"))
            await service.prepare_session("conn-1", "rec-1")
            await service.process(RawChunk("serial", "serial", headset_frame(), 601, 1, "conn-1", "trace-1"))
            flushing = asyncio.create_task(service.flush())
            await asyncio.wait_for(algorithm.started.wait(), 0.1)
            disconnected = asyncio.create_task(
                service.on_connection(DeviceConnectionEvent(ConnectionState.RECONNECTING, 700, reason="unplugged"))
            )
            await asyncio.sleep(0)
            algorithm.release.set()
            await asyncio.gather(flushing, disconnected)

            self.assertEqual(sink.events, [])
            self.assertEqual(service.data.snapshot.data_state, DataState.UNAVAILABLE)
            self.assertEqual(service.diagnostics["published"], 0)
            await service.close()
            await repository.close()

    async def test_data_state_recovers_after_stream_becomes_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SegmentedRecordingRepository(directory, warning_threshold_bytes=2, critical_threshold_bytes=1)
            service = ApplicationService(
                device_protocol="headset_rev181",
                parser=HeadsetRev181Parser(),
                algorithm=FakeAlgorithm(),
                snapshots=InMemoryLatestSnapshotStore(),
                northbound=CollectingNorthboundSink(),
                recording=repository,
                interval_ms=10,
                stale_after_ms=25,
            )
            await service.prepare_session("conn-1", "rec-1")
            await service.process(RawChunk("serial", "serial", headset_frame(1), 10, 1, "conn-1", "trace-1"))
            await service.flush()
            self.assertEqual(service.data.snapshot.data_state, DataState.STREAMING)

            await asyncio.sleep(0.04)
            self.assertEqual(service.data.snapshot.data_state, DataState.STALE)
            self.assertEqual(service.data.snapshot.recent_error, "DATA_STALE")

            await service.process(RawChunk("serial", "serial", headset_frame(2), 30, 2, "conn-1", "trace-2"))
            await service.flush()
            self.assertEqual(service.data.snapshot.data_state, DataState.STREAMING)
            self.assertIsNone(service.data.snapshot.recent_error)
            self.assertEqual(service.data.snapshot.counters["produced_windows"], 2)
            await service.close()
            await repository.close()

    async def test_unavailable_algorithm_does_not_block_valid_raw_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "gateway.toml"
            config_path.write_text(
                '[data_source]\ntype="bluetooth"\n'
                f'[recording]\ndirectory="{directory}"\n',
                encoding="utf-8",
            )
            gateway = Gateway(load(config_path))
            await gateway.start()
            await gateway.update_status("connectionState", "connected")
            recording_id = gateway.store.recording_id
            assert recording_id is not None
            service = ApplicationService(
                device_protocol="headband_ble",
                parser=HeadbandBleParser(),
                algorithm=FakeUnavailableAlgorithm(),
                snapshots=gateway.latest_snapshot,
                northbound=GatewayNorthboundSink(gateway),
                recording=gateway.recording_repository,
            )
            self.assertEqual(
                await service.prepare_session("conn-1", recording_id),
                AlgorithmState.UNAVAILABLE,
            )
            self.assertEqual(service.data.snapshot.data_state, DataState.READY)
            sent = []

            async def send(value) -> None:
                sent.append(value)

            session = ClientSession()
            await gateway.subscribe(
                session,
                {"streams": ["eeg.raw"], "includeInvalid": False},
                send,
            )
            await service.process(RawChunk("bluetooth", "ff31", bytes(range(20)), 601, 1, "conn-1", "trace-1"))
            await service.flush()
            await asyncio.sleep(0.01)
            self.assertEqual(service.data.snapshot.data_state, DataState.STREAMING)
            self.assertEqual(len(sent), 1)
            self.assertTrue(sent[0]["data"]["valid"])
            self.assertEqual(sent[0]["data"]["payload"]["eegRaw"]["byteLength"], 20)
            await gateway.close_session(session)
            await service.close()
            await gateway.stop()

    async def test_algorithm_timeout_publishes_invalid_event_to_algorithm_subscriber(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "gateway.toml"
            config_path.write_text(
                '[data_source]\ntype="bluetooth"\n'
                f'[recording]\ndirectory="{directory}"\n',
                encoding="utf-8",
            )
            gateway = Gateway(load(config_path))
            await gateway.start()
            await gateway.update_status("connectionState", "connected")
            recording_id = gateway.store.recording_id
            assert recording_id is not None
            service = ApplicationService(
                device_protocol="headband_ble",
                parser=HeadbandBleParser(),
                algorithm=SlowAlgorithm(),
                snapshots=gateway.latest_snapshot,
                northbound=GatewayNorthboundSink(gateway),
                recording=gateway.recording_repository,
                algorithm_timeout_ms=1,
            )
            await service.prepare_session("conn-1", recording_id)
            await gateway.update_status("algorithmState", "ready")
            sent = []

            async def send(value) -> None:
                sent.append(value)

            session = ClientSession()
            await gateway.subscribe(session, {"streams": ["eeg"], "includeInvalid": True}, send)
            await service.process(RawChunk("bluetooth", "ff31", bytes(range(20)), 601, 1, "conn-1", "trace-1"))
            await service.flush()
            await asyncio.sleep(0.01)

            self.assertEqual(len(sent), 1)
            self.assertFalse(sent[0]["data"]["valid"])
            self.assertEqual(sent[0]["data"]["payload"]["algorithm"], {})
            self.assertEqual(sent[0]["data"]["payload"]["invalidReasons"], ["ALGORITHM_TIMEOUT"])
            await gateway.close_session(session)
            await service.close()
            await gateway.stop()

    async def test_failed_batch_write_marks_published_result_not_guaranteed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SegmentedRecordingRepository(directory, warning_threshold_bytes=2, critical_threshold_bytes=1)
            original_write = repository._write

            def fail_parsed(record) -> None:
                if record.record_type == "parsed.signal_batch":
                    raise OSError("disk failure")
                original_write(record)

            repository._write = fail_parsed
            sink = CollectingNorthboundSink()
            service = ApplicationService(
                device_protocol="headset_rev181",
                parser=HeadsetRev181Parser(),
                algorithm=FakeAlgorithm(),
                snapshots=InMemoryLatestSnapshotStore(),
                northbound=sink,
                recording=repository,
            )
            await service.prepare_session("conn-1", "rec-1")
            await service.process(RawChunk("serial", "serial", headset_frame(), 601, 1, "conn-1", "trace-1"))
            await service.flush()

            self.assertEqual(len(sink.events), 1)
            assert sink.events[0].result is not None
            self.assertFalse(sink.events[0].result.persistence_guaranteed)
            await service.close()
            await repository.close()

    async def test_gateway_sink_preserves_complete_ble_window_and_replay_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "gateway.toml"
            config_path.write_text(
                '[data_source]\ntype="bluetooth"\n'
                f'[recording]\ndirectory="{directory}"\n',
                encoding="utf-8",
            )
            gateway = Gateway(load(config_path))
            await gateway.start()
            await gateway.update_status("connectionState", "connected")
            recording_id = gateway.store.recording_id
            assert recording_id is not None
            service = ApplicationService(
                device_protocol="headband_ble",
                parser=HeadbandBleParser(),
                algorithm=FakeAlgorithm(),
                snapshots=gateway.latest_snapshot,
                northbound=GatewayNorthboundSink(gateway),
                recording=gateway.recording_repository,
            )
            await service.prepare_session("conn-1", recording_id)
            for index, received_at_ms in enumerate((601, 602), start=1):
                await service.process(
                    RawChunk("bluetooth", "ff31", bytes([index]) * 20, received_at_ms, index, "conn-1", f"trace-{index}")
                )
            await service.process(RawChunk("bluetooth", "ff51", b"\x48", 603, 3, "conn-1", "trace-3"))
            await service.flush()

            snapshot = gateway.latest_snapshot.get(frozenset({"eeg"}))
            assert snapshot.version is not None
            self.assertEqual(snapshot.version.value, 1)
            events = gateway.store.events(recording_id)
            raw_event = next(event for event in events if "eegRaw" in event["payload"])
            algorithm_event = next(event for event in events if "algorithm" in event["payload"])
            self.assertEqual(raw_event["payload"]["eegRaw"]["packetCount"], 2)
            self.assertEqual(raw_event["payload"]["eegRaw"]["byteLength"], 40)
            self.assertEqual(raw_event["payload"]["hrRaw"]["byteLength"], 1)
            self.assertEqual(algorithm_event["payload"]["algorithm"]["attention"], 42)
            await service.close()
            await gateway.stop()
