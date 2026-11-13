from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import websockets

from neurobridge.adapters.northbound.publisher import CollectingNorthboundSink, GatewayNorthboundSink
from neurobridge.adapters.parsers import HeadbandBleParser, HeadsetRev181Parser
from neurobridge.adapters.storage import SegmentedRecordingRepository
from neurobridge.application.service import ApplicationService
from neurobridge.application.snapshots import InMemoryLatestSnapshotStore
from neurobridge.business.gateway import ClientSession, Gateway
from neurobridge.ble.flowtime import FF21, FF31, FF51
from neurobridge.bootstrap import build_runtime
from neurobridge.bootstrap.container import _ApplicationPipelineAdapter, build_container
from neurobridge.config import load
from neurobridge.domain.algorithm import AlgorithmResult, AlgorithmState
from neurobridge.domain.raw import RawChunk
from neurobridge.domain.result import WindowResult
from neurobridge.domain.signal import ParsedSignalBatch
from neurobridge.domain.status import ConnectionState, DataState, DeviceConnectionEvent
from neurobridge.northbound.websocket import SUBPROTOCOL, create_server
from neurobridge.ports.raw_source import SourceStatus
from neurobridge.profiles import RuntimePlatform
from neurobridge.versioning import LEGACY_NORTHBOUND_PROTOCOL_VERSION, NORTHBOUND_PROTOCOL_VERSION


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

    @property
    def available(self) -> bool:
        return hasattr(self, "session") and not self.closed

    async def stop(self) -> None:
        await self.close()


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
    async def test_windows_calibration_reaches_only_the_headset_algorithm(self) -> None:
        root = Path(__file__).resolve().parents[2]
        template = (root / "windows/gateway.toml.example").read_text(encoding="utf-8")
        config_path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "gateway.toml"
        config_path.write_text(template.replace("eeg_alignment_percent = 0", "eeg_alignment_percent = 5"), encoding="utf-8")
        config = load(config_path)
        config = replace(config, recording=replace(config.recording, directory=config_path.parent / "recordings"))
        (headset, headband), _ = build_runtime(config, RuntimePlatform("windows", "x86_64"))

        for container, session in ((headset, "serial"), (headband, "ble")):
            app = container.application
            app.algorithm = FakeAlgorithm()
            app.control = None
            app.northbound = CollectingNorthboundSink()
            self.addAsyncCleanup(container.gateway.stop)
            await container.gateway.start()
            await container.gateway.update_status("connectionState", "validated" if session == "serial" else "connected")
            app.bind_recording(container.gateway.recording_repository)
            self.addAsyncCleanup(app.close)
            await app.on_connection(DeviceConnectionEvent(ConnectionState.CONNECTED, 600, session))
            await app.prepare_session(session, container.gateway.store.recording_id)

        def eeg_bytes(samples):
            return b"\x00\x01" + b"".join(sample.to_bytes(3, "big") for sample in samples)

        original = eeg_bytes([900, 1800, 1000, 2000, 1100, 2200])
        reference = eeg_bytes([400, 400, 1000, 1000, 1600, 1600])
        now = int(time.time() * 1000)
        with (
            patch.object(headband.source, "status", return_value=SourceStatus(ConnectionState.CONNECTED, "ble")),
            self.assertLogs("neurobridge.application", level="DEBUG") as logs,
        ):
            await headband.application.process(RawChunk("bluetooth", "ff31", reference, now, 0, "ble", "ble-trace"))
            await headband.application.flush()
            frame = b"\xaa\xaa\xaa\x1c" + original + b"\x48\xbb\xbb\xbb"
            await headset.application.process(RawChunk("serial", "serial", frame, now, 0, "serial", "serial-trace"))
            await headset.application.flush()

        headband_value, = headband.application.algorithm.inputs
        headset_value, = headset.application.algorithm.inputs
        self.assertEqual(base64.b64decode(headband_value.payload["eegRawBase64"]), reference)
        self.assertEqual(base64.b64decode(headset_value.payload["eegRawBase64"]),
                         eeg_bytes([895, 1790, 1000, 2000, 1105, 2210]))
        self.assertEqual(base64.b64decode(headset_value.payload["hrRawBase64"]), b"\x48")
        headset_window, = headset.application.northbound.events
        self.assertEqual(headset_window.result.batch.signals[0].samples, original[2:])
        self.assertEqual(headset.application.diagnostics["window_processing_errors"], 0)
        self.assertEqual(headband.application.diagnostics["window_processing_errors"], 0)
        self.assertTrue(any(f"alignment decision: batchId={headset_value.batch_id}" in line
                            and "reason=applied" in line and "gains=(1.05, 1.05)" in line for line in logs.output))
        self.assertTrue(any(f"algorithm response: batchId={headset_value.batch_id}" in line
                            and "mappingVersion=2 valid=True" in line for line in logs.output))
        self.assertFalse(any(headset_value.payload["eegRawBase64"] in line for line in logs.output))

    async def test_windows_two_sources_share_one_websocket_and_independent_subscriptions(self) -> None:
        root = Path(__file__).resolve().parents[2]
        template = (root / "windows/gateway.toml.example").read_text(encoding="utf-8")
        directory = self.enterContext(tempfile.TemporaryDirectory())
        config_path = Path(directory) / "gateway.toml"
        config_path.write_text(template, encoding="utf-8")
        config = load(config_path)
        config = replace(config, server=replace(config.server, port=0),
                         recording=replace(config.recording, directory=Path(directory) / "recordings"))
        (headset, headband), router = build_runtime(config, RuntimePlatform("windows", "x86_64"))
        self.assertIsNot(headset.application.algorithm, headband.application.algorithm)
        self.assertIsNot(headset.gateway.latest_snapshot, headband.gateway.latest_snapshot)
        self.assertNotEqual(headset.config.recording.directory, headband.config.recording.directory)

        for pipeline, state in ((headset, "validated"), (headband, "connected")):
            self.addAsyncCleanup(pipeline.gateway.stop)
            await pipeline.gateway.start()
            await pipeline.gateway.update_status("connectionState", state)
            await pipeline.gateway.update_status("algorithmState", "ready")
        server = await create_server(headset.gateway, router=router)
        self.addAsyncCleanup(server.wait_closed)
        # Close the listener before waiting for it during async cleanup.
        self.addAsyncCleanup(server.close)
        port = server.sockets[0].getsockname()[1]

        async def publish(pipeline, attention: int) -> None:
            now = int(time.time() * 1000)
            protocol = pipeline.profile.device_protocol
            batch = ParsedSignalBatch(protocol, protocol, protocol, "test", now - 600, now, (), ())
            algorithm = AlgorithmResult(batch.batch_id, "test", now, now, {"attention": attention})
            await pipeline.gateway.publish_window_result(WindowResult(batch, algorithm, "live", now, True))

        async with websockets.connect(
            f"ws://127.0.0.1:{port}{config.server.path}", subprotocols=[SUBPROTOCOL],
            origin=f"http://127.0.0.1:{config.local_ui.port}", ping_interval=None,
        ) as ws:
            async def receive():
                return json.loads(await asyncio.wait_for(ws.recv(), 2))

            async def request(action, params, version=NORTHBOUND_PROTOCOL_VERSION):
                await ws.send(json.dumps({"protocolVersion": version, "messageType": "request",
                                          "requestId": action, "action": action, "params": params}))
                return await receive()

            legacy = await request("getStatus", {}, LEGACY_NORTHBOUND_PROTOCOL_VERSION)
            self.assertEqual(legacy["protocolVersion"], LEGACY_NORTHBOUND_PROTOCOL_VERSION)
            self.assertEqual(legacy["data"]["result"]["gatewayBootId"], headset.gateway.boot_id)
            self.assertNotIn("source", legacy["data"])
            invalid = await request("getStatus", {"source": []})
            self.assertEqual(invalid["code"], 400)
            self.assertEqual(invalid["data"]["requestId"], "getStatus")
            subscriptions = {}
            for source in ("headset", "headband"):
                response = await request("subscribe", {"source": source, "streams": ["eeg"]})
                self.assertEqual(response["code"], 200)
                self.assertEqual(response["data"]["source"], source)
                subscriptions[source] = response["data"]["result"]["subscriptionId"]
            await publish(headset, 42)
            await publish(headband, 73)
            events = [(await receive())["data"] for _ in subscriptions]
            self.assertEqual({
                event["source"]: (event["subscriptionId"], event["payload"]["algorithm"]["attention"])
                for event in events
            }, {"headset": (subscriptions["headset"], 42), "headband": (subscriptions["headband"], 73)})
            latest = await request("getLatest", {"source": "headband", "streams": ["eeg"]})
            self.assertEqual(latest["data"]["result"]["payload"]["algorithm"]["attention"], 73)
            wrong = await request("unsubscribe", {"source": "headband", "subscriptionId": subscriptions["headset"]})
            self.assertEqual(wrong["code"], 404)
            stopped = await request("unsubscribe", {"source": "headset", "subscriptionId": subscriptions["headset"]})
            self.assertEqual(stopped["code"], 200)
            await publish(headset, 43)
            await publish(headband, 74)
            event = (await receive())["data"]
            self.assertEqual(event["source"], "headband")
            self.assertEqual(event["payload"]["algorithm"]["attention"], 74)
        server.close()
        await server.wait_closed()
        self.assertFalse(headset.gateway.sessions)
        self.assertFalse(headband.gateway.sessions)

    async def test_windows_ble_headband_to_live_websocket_and_recording(self) -> None:
        started = asyncio.Event()
        notifications = {}
        commands = []
        candidate = SimpleNamespace(address="headband", name="Flowtime Headband")
        advertisement = SimpleNamespace(
            local_name="Flowtime Headband", rssi=-40,
            service_uuids=["0000ff10-1212-abcd-1523-785feabcd123"],
        )

        class FakeClient:
            def __init__(self, device, *, disconnected_callback):
                self.services = SimpleNamespace(get_characteristic=lambda _: None)
                self.is_connected = False
                self.device = device

            async def connect(self):
                self.is_connected = True

            async def start_notify(self, characteristic, callback):
                notifications[characteristic] = callback

            async def write_gatt_char(self, characteristic, command, *, response):
                commands.append((characteristic, command, response))
                if command == b"\x05":
                    started.set()

            async def disconnect(self):
                self.is_connected = False

        scanner = SimpleNamespace(discover=AsyncMock(return_value={candidate.address: (candidate, advertisement)}))
        bleak = SimpleNamespace(BleakScanner=scanner, BleakClient=FakeClient)
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "gateway.toml"
            template = (root / "windows/gateway.toml.example").read_text(encoding="utf-8")
            config_path.write_text(
                template.replace('profile = "windows_headset_local"', 'profile = "windows_headband_local"')
                .replace('\ntype = "serial"', '\ntype = "bluetooth"')
                .replace('include_headband = true', 'include_headband = false'),
                encoding="utf-8",
            )
            config = load(config_path)
            config = replace(config, server=replace(config.server, port=0),
                             recording=replace(config.recording, directory=Path(directory) / "recordings"))

            with patch.dict(sys.modules, {"bleak": bleak}), patch(
                "neurobridge.bootstrap.container.AffectiveSdkAlgorithmEngine", return_value=FakeAlgorithm(),
            ):
                container = build_container(config, RuntimePlatform("windows", "x86_64"))
                gateway = container.gateway
                adapter = container.device_adapter
                await gateway.start()
                server = await create_server(gateway, container.northbound_controller)
                port = server.sockets[0].getsockname()[1]
                adapter_task = None
                try:
                    async with websockets.connect(
                        f"ws://127.0.0.1:{port}{config.server.path}", subprotocols=[SUBPROTOCOL],
                        origin=f"http://127.0.0.1:{config.local_ui.port}", ping_interval=None,
                    ) as ws:
                        async def request(action, params):
                            await ws.send(json.dumps({"protocolVersion": "1.0", "messageType": "request",
                                                      "requestId": action, "action": action, "params": params}))
                            return json.loads(await asyncio.wait_for(ws.recv(), 2))

                        offline = await request("getLatest", {})
                        self.assertEqual(offline["code"], 409)
                        self.assertIn("Bluetooth headband", offline["message"])
                        self.assertIsNone(gateway.store.recording_id)
                        adapter_task = asyncio.create_task(adapter.run())
                        await asyncio.wait_for(started.wait(), 2)
                        status = (await request("getStatus", {}))["data"]["result"]
                        self.assertEqual((status["connectionState"], status["mode"]), ("connected", "live"))
                        subscribed = await request("subscribe", {"streams": ["eeg", "eeg.raw", "hr.raw"]})
                        self.assertEqual(subscribed["code"], 200)
                        eeg, hr = bytes(range(20)), b"\x48"
                        timestamp_ms = (int(time.time() * 1000) // 600 + 1) * 600
                        with patch("neurobridge.device.packet.wall_clock_ms", return_value=timestamp_ms):
                            for characteristic, value in ((FF31, eeg), (FF51, hr)):
                                notifications[characteristic](0, bytearray(value))
                            event = json.loads(await asyncio.wait_for(ws.recv(), 3))["data"]
                        self.assertEqual(event["mode"], "live")
                        self.assertEqual(event["payload"]["algorithm"]["attention"], 42)
                        self.assertEqual(base64.b64decode(event["payload"]["eegRaw"]["bytesBase64"]), eeg)
                        self.assertEqual(base64.b64decode(event["payload"]["hrRaw"]["bytesBase64"]), hr)
                finally:
                    server.close()
                    await server.wait_closed()
                    if adapter_task is not None:
                        adapter_task.cancel()
                        await asyncio.gather(adapter_task, return_exceptions=True)
                        await adapter.stop()
                    await gateway.stop()

            self.assertEqual(commands, [(FF21, b"\x05", True), (FF21, b"\x06", True)])
            records = [json.loads(line) for path in config.recording.directory.glob("sessions/*/*/*.jsonl")
                       for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertIn("raw.device_frame", {row["recordType"] for row in records})
            result = next(row["payload"] for row in records if row["recordType"] == "algorithm.result")
            self.assertEqual((result["deviceProtocol"], result["sourceType"]), ("headband_ble", "bluetooth"))

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
            wrong_source = await service.process(RawChunk("bluetooth", "ff31", frame, 601, 1, "conn-1", "trace-ble"))
            self.assertEqual(wrong_source.discarded_bytes, len(frame))
            self.assertEqual(wrong_source.frames, ())
            await service.process(RawChunk("serial", "serial", frame, 601, 1, "conn-1", "trace-1"))
            await service.flush()
            self.assertEqual(len(sink.events), 1)
            result = sink.events[0].result
            assert result is not None
            self.assertEqual(result.batch.connection_session_id, "conn-1")
            self.assertEqual(result.batch.frame_refs, tuple(result.batch.signals[0].frame_refs))
            self.assertEqual(result.algorithm_result.metrics["attention"], 42)
            self.assertEqual(len(algorithm.inputs), 1)
            self.assertEqual(algorithm.inputs[0].device_protocol, "headset_rev181")
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
            self.assertEqual((algorithm_row["payload"]["deviceProtocol"], algorithm_row["payload"]["sourceType"]),
                             ("headset_rev181", "serial"))
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
