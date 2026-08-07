from __future__ import annotations

import asyncio
import base64
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from neurobridge.config import AlgorithmConfig, BleConfig, GatewayConfig, RecordingConfig, ServerConfig
from neurobridge.algorithm.runner import AlgorithmRunner
from neurobridge.ble.flowtime import FF52, REQUIRED_NOTIFICATION_CHARACTERISTICS, FlowtimeAdapter, wear_state_from_packet
from neurobridge.business.gateway import ClientSession, Gateway, REPLAY_NOT_AVAILABLE_REASON
from neurobridge.ble.packets import DataWindow, EEG_PACKET_BYTES, HR_NATIVE_PACKET_BYTES, HR_RAW_PACKET_BYTES, RawPacket, WindowAssembler
from neurobridge.business.recording import RecordingStore


def config(root: Path, replay_id: str | None = None, replay_speed: float = 1000) -> GatewayConfig:
    return GatewayConfig(ServerConfig("127.0.0.1", 8765, "/neurobridge/v1/ws"), BleConfig(False, "Flowtime Headband", "0000ff10-1212-abcd-1523-785feabcd123", 5, 3), RecordingConfig(root, "SUBJECT-001", replay_id, replay_speed), AlgorithmConfig(False, ()))


class PacketTests(unittest.TestCase):
    def test_window_keeps_original_bytes_and_counts(self) -> None:
        assembler = WindowAssembler()
        assembler.add("ff31", b"a" * EEG_PACKET_BYTES, 1000)
        assembler.add("ff51", b"n" * HR_NATIVE_PACKET_BYTES, 1010)
        assembler.add("ff52", b"b" * HR_RAW_PACKET_BYTES, 1020)
        windows = assembler.add("ff31", b"c" * EEG_PACKET_BYTES, 1601)
        self.assertEqual(len(windows), 1)
        payload = windows[0].raw_payload()
        self.assertEqual(payload["eegRaw"]["packetCount"], 1)
        self.assertEqual(base64.b64decode(payload["eegRaw"]["bytesBase64"]), b"a" * EEG_PACKET_BYTES)
        self.assertEqual(payload["hrRaw"]["packetCount"], 1)

    def test_confirmed_packet_lengths_and_ff52_raw_stream_are_preserved(self) -> None:
        self.assertEqual((EEG_PACKET_BYTES, HR_NATIVE_PACKET_BYTES, HR_RAW_PACKET_BYTES), (14, 16, 20))
        self.assertIn(FF52, REQUIRED_NOTIFICATION_CHARACTERISTICS)
        assembler = WindowAssembler()
        assembler.add("ff51", b"n" * HR_NATIVE_PACKET_BYTES, 1000)
        assembler.add("ff52", b"r" * HR_RAW_PACKET_BYTES, 1010)
        window = assembler.add("ff31", b"e" * EEG_PACKET_BYTES, 1601)[0]
        self.assertIn("hrRaw", window.raw_payload())
        self.assertEqual(window.raw_payload()["hrRaw"]["packetBytes"], HR_RAW_PACKET_BYTES)
        self.assertEqual(window.hr_native[0].value, b"n" * HR_NATIVE_PACKET_BYTES)


class FlowtimeSelectionTests(unittest.TestCase):
    def test_selects_matching_candidate_with_highest_rssi(self) -> None:
        class Device:
            def __init__(self, name: str, uuids: list[str], rssi: int) -> None:
                self.name, self.metadata, self.rssi = name, {"uuids": uuids}, rssi
        async def ignored_packet(_: str, __: bytes) -> None: pass
        async def ignored_status(_: str, __: object) -> None: pass
        async def ignored_ready() -> None: pass
        adapter = FlowtimeAdapter(config(Path("/tmp")).ble, ignored_packet, ignored_status, ignored_ready)
        selected = adapter.select_strongest([
            Device("Other", ["0000ff10-1212-abcd-1523-785feabcd123"], -20),
            Device("Flowtime Headband", ["0000ff10-1212-abcd-1523-785feabcd123"], -80),
            Device("Flowtime Headband", ["0000ff10-1212-abcd-1523-785feabcd123"], -42),
        ])
        self.assertEqual(selected.rssi, -42)

    def test_ff32_remains_unknown_until_its_values_are_poc_verified(self) -> None:
        self.assertEqual(wear_state_from_packet(b"\x00\x00"), "unknown")
        self.assertEqual(wear_state_from_packet(b"\x01\x02"), "unknown")


class FlowtimeSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_ff52_notification_is_subscribed_and_forwarded_as_raw_hr(self) -> None:
        received: list[tuple[str, bytes]] = []

        async def packet(characteristic: str, value: bytes) -> None:
            received.append((characteristic, value))

        async def ignored_status(_: str, __: object) -> None:
            pass

        async def ignored_ready() -> None:
            pass

        class Services:
            @staticmethod
            def get_characteristic(_: str) -> None:
                return None

        class Client:
            def __init__(self) -> None:
                self.services = Services()
                self.handlers: dict[str, object] = {}

            async def start_notify(self, characteristic: str, handler: object) -> None:
                self.handlers[characteristic] = handler

        adapter = FlowtimeAdapter(config(Path("/tmp")).ble, packet, ignored_status, ignored_ready)
        client = Client()
        adapter._client = client
        await adapter._subscribe()
        self.assertEqual(set(client.handlers), set(REQUIRED_NOTIFICATION_CHARACTERISTICS))
        client.handlers[FF52](0, bytearray(b"r" * HR_RAW_PACKET_BYTES))  # type: ignore[operator]
        await asyncio.sleep(0)
        self.assertEqual(received, [("ff52", b"r" * HR_RAW_PACKET_BYTES)])


class AlgorithmRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_broken_algorithm_bridge_is_reported_without_raising(self) -> None:
        class BrokenStdin:
            def write(self, _: bytes) -> None:
                pass
            async def drain(self) -> None:
                raise BrokenPipeError("bridge exited")
        class Process:
            returncode = None
            stdin = BrokenStdin()
            stdout = object()
        runner = AlgorithmRunner(AlgorithmConfig(True, ("bridge",)))
        runner.process = Process()  # type: ignore[assignment]
        window = DataWindow(0, 600)
        window.append(RawPacket("ff31", 100, b"x" * EEG_PACKET_BYTES))
        payload, reasons = await runner.evaluate(window)
        self.assertIsNone(payload)
        self.assertEqual(reasons, ["ALGORITHM_ERROR"])
        self.assertIn("bridge exited", runner.error or "")

    async def test_bridge_reported_error_marks_window_invalid(self) -> None:
        class Stdin:
            def write(self, _: bytes) -> None:
                pass
            async def drain(self) -> None:
                pass
        class Stdout:
            async def readline(self) -> bytes:
                return b'{"algorithm":{},"bridgeError":"invalid EEG group"}\n'
        class Process:
            returncode = None
            stdin = Stdin()
            stdout = Stdout()
        runner = AlgorithmRunner(AlgorithmConfig(True, ("bridge",)))
        runner.process = Process()  # type: ignore[assignment]
        window = DataWindow(0, 600)
        window.append(RawPacket("ff31", 100, b"x" * EEG_PACKET_BYTES))
        payload, reasons = await runner.evaluate(window)
        self.assertIsNone(payload)
        self.assertEqual(reasons, ["ALGORITHM_ERROR"])
        self.assertIn("invalid EEG group", runner.error or "")


class AlgorithmLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_algorithm_is_initialized_for_each_device_ready_event(self) -> None:
        class FakeAlgorithm:
            available = True
            error = None
            def __init__(self) -> None:
                self.initializations = 0
            async def initialize(self) -> None:
                self.initializations += 1
            async def stop(self) -> None:
                pass
        with tempfile.TemporaryDirectory() as directory:
            gateway = Gateway(config(Path(directory)))
            fake = FakeAlgorithm()
            gateway.algorithm = fake
            await gateway.start()
            self.assertEqual(fake.initializations, 0)
            await gateway.on_device_ready()
            self.assertEqual(fake.initializations, 1)
            self.assertEqual(gateway.status["algorithmState"], "ready")


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_error_is_retained_until_a_successful_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gateway = Gateway(config(Path(directory)))
            await gateway.update_connection_error("headband not found")
            self.assertEqual(gateway.connection_error, "headband not found")

            await gateway.update_status("connectionState", "connected")
            self.assertIsNone(gateway.connection_error)
            await gateway.stop()

    async def test_gateway_persists_all_confirmed_raw_characteristics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gateway = Gateway(config(root))
            await gateway.update_status("connectionState", "connected")
            recording_id = gateway.store.recording_id
            self.assertIsNotNone(recording_id)
            await gateway.receive_packet("ff31", b"e" * EEG_PACKET_BYTES)
            await gateway.receive_packet("ff51", b"n" * HR_NATIVE_PACKET_BYTES)
            await gateway.receive_packet("ff52", b"r" * HR_RAW_PACKET_BYTES)
            await gateway.update_status("connectionState", "disconnected")

            raw_dir = root / "sessions" / str(recording_id) / "raw"
            for stream, value in (("eeg", b"e" * EEG_PACKET_BYTES), ("hr_native", b"n" * HR_NATIVE_PACKET_BYTES), ("hr", b"r" * HR_RAW_PACKET_BYTES)):
                row = json.loads((raw_dir / f"{stream}.jsonl").read_text(encoding="utf-8"))
                self.assertEqual(base64.b64decode(row["bytesBase64"]), value)

    async def test_window_observer_receives_algorithm_output(self) -> None:
        class Algorithm:
            available = True
            error = None
            async def evaluate(self, _: DataWindow) -> tuple[dict, list[str]]:
                return {"attention": 7.0}, []
            async def stop(self) -> None:
                pass
        with tempfile.TemporaryDirectory() as directory:
            gateway = Gateway(config(Path(directory)))
            gateway.algorithm = Algorithm()
            observed: list[tuple[dict | None, list[str], bool]] = []
            async def observe(_: DataWindow, algorithm: dict | None, reasons: list[str], valid: bool) -> None:
                observed.append((algorithm, reasons, valid))
            gateway.window_observer = observe
            window = DataWindow(0, 600)
            window.append(RawPacket("ff31", 100, b"x" * EEG_PACKET_BYTES))
            await gateway.publish_window(window)
            self.assertEqual(observed, [({"attention": 7.0}, [], True)])

    async def test_subscribe_raw_live_event_has_contract_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gateway = Gateway(config(Path(directory)))
            await gateway.update_status("connectionState", "connected")
            sent: list[dict] = []
            async def send(item: dict) -> None:
                sent.append(item)
            session = ClientSession()
            gateway.sessions.add(session)
            await gateway.handle(session, '{"protocolVersion":"1.0","messageType":"request","requestId":"1","action":"subscribe","params":{"streams":["eeg.raw"]}}', send)
            subscription_id = sent.pop()["data"]["result"]["subscriptionId"]
            window = DataWindow(600, 1200)
            window.append(RawPacket("ff31", 1000, b"x" * EEG_PACKET_BYTES))
            await gateway.publish_window(window)
            event = next(item for item in sent if item["data"].get("event") == "data")
            self.assertEqual(set(event), {"protocolVersion", "code", "data", "message"})
            self.assertEqual(event["data"]["subscriptionId"], subscription_id)
            self.assertEqual(event["data"]["payload"]["eegRaw"]["packetBytes"], EEG_PACKET_BYTES)
            await gateway.close_session(session)

    def test_algorithm_streams_preserve_the_nested_contract_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gateway = Gateway(config(Path(directory)))
            algorithm = {
                "eeg": {"bandPower": {"alpha": 1.2}},
                "sleep": {"updated": False, "stage": 0},
                "attention": 7.0,
                "hr": {"value": 72, "hrv": 0.4},
                "pressure": 2.0,
                "coherence": 0.5,
                "arousal": 1.0,
            }
            self.assertEqual(
                gateway.filtered_payload({}, algorithm, frozenset({"eeg"})),
                {"algorithm": {"eeg": {"bandPower": {"alpha": 1.2}}, "sleep": {"updated": False, "stage": 0}, "attention": 7.0}},
            )
            self.assertEqual(
                gateway.filtered_payload({}, algorithm, frozenset({"hr"})),
                {"algorithm": {"hr": {"value": 72, "hrv": 0.4}, "pressure": 2.0, "coherence": 0.5, "arousal": 1.0}},
            )

    async def test_invalid_request_is_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gateway = Gateway(config(Path(directory)))
            sent: list[dict] = []
            async def send(item: dict) -> None:
                sent.append(item)
            await gateway.handle(ClientSession(), '{"protocolVersion":"2.0"}', send)
            self.assertEqual(sent[0]["code"], 400)
            self.assertEqual(sent[0]["data"]["reason"], "INVALID_REQUEST")

    async def test_algorithm_failure_does_not_block_raw_recording(self) -> None:
        class FailedAlgorithm:
            available = False
            error = "bridge exited"
            async def evaluate(self, _: DataWindow) -> tuple[None, list[str]]:
                return None, ["ALGORITHM_ERROR"]
            async def stop(self) -> None:
                pass
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gateway = Gateway(config(root))
            gateway.algorithm = FailedAlgorithm()
            await gateway.update_status("connectionState", "connected")
            await gateway.receive_packet("ff31", b"x" * EEG_PACKET_BYTES)
            window = DataWindow(0, 600)
            window.append(RawPacket("ff31", 100, b"x" * EEG_PACKET_BYTES))
            await gateway.publish_window(window)
            recording_id = gateway.store.recording_id
            self.assertIsNotNone(recording_id)
            self.assertTrue((root / "sessions" / str(recording_id) / "raw" / "eeg.jsonl").exists())
            self.assertEqual(gateway.status["algorithmState"], "error")

    async def test_final_window_flushes_without_a_following_packet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gateway = Gateway(config(Path(directory)))
            gateway.assembler = WindowAssembler(interval_ms=10)
            await gateway.update_status("connectionState", "connected")
            sent: list[dict] = []
            async def send(item: dict) -> None:
                sent.append(item)
            session = ClientSession()
            gateway.sessions.add(session)
            await gateway.handle(session, '{"protocolVersion":"1.0","messageType":"request","requestId":"timer-1","action":"subscribe","params":{"streams":["eeg.raw"]}}', send)
            sent.clear()
            await gateway.receive_packet("ff31", b"x" * EEG_PACKET_BYTES)
            await asyncio.sleep(0.05)
            self.assertTrue(any(item["data"].get("event") == "data" for item in sent))
            await gateway.close_session(session)
            await gateway.stop()


class RecordingTests(unittest.TestCase):
    def test_confirmed_raw_profiles_are_persisted_and_replayed_with_correct_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RecordingStore(Path(directory))
            recording_id = store.start()
            store.save_raw_packet(stream="eeg", received_at_ms=1190, window_start_ms=600, window_end_ms=1200, value=b"e" * EEG_PACKET_BYTES)
            store.save_raw_packet(stream="hr_native", received_at_ms=1195, window_start_ms=600, window_end_ms=1200, value=b"n" * HR_NATIVE_PACKET_BYTES)
            store.save_raw_packet(stream="hr", received_at_ms=1199, window_start_ms=600, window_end_ms=1200, value=b"r" * HR_RAW_PACKET_BYTES)

            session = Path(directory) / "sessions" / recording_id
            self.assertTrue((session / "raw" / "hr_native.jsonl").is_file())
            event = store.events(recording_id)[0]
            self.assertTrue(event["valid"])
            self.assertEqual(event["payload"]["eegRaw"]["packetBytes"], EEG_PACKET_BYTES)
            self.assertEqual(event["payload"]["hrRaw"]["packetBytes"], HR_RAW_PACKET_BYTES)
            self.assertEqual(base64.b64decode(event["payload"]["eegRaw"]["bytesBase64"]), b"e" * EEG_PACKET_BYTES)
            self.assertEqual(base64.b64decode(event["payload"]["hrRaw"]["bytesBase64"]), b"r" * HR_RAW_PACKET_BYTES)

    def test_raw_and_algorithm_are_separate_then_replay_merges_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RecordingStore(Path(directory))
            recording_id = store.start()
            store.save_raw_packet(stream="eeg", received_at_ms=1199, window_start_ms=600, window_end_ms=1200, value=b"e" * EEG_PACKET_BYTES)
            source = {"receivedAtMsStart": 1199, "receivedAtMsEnd": 1200, "packetCount": 1, "windowStartMs": 600, "windowEndMs": 1200}
            store.save_algorithm_events(algorithm={"attention": 7.0}, computed_at_ms=1201, eeg_source=source, hr_source=None, valid=True, invalid_reasons=[])
            session = Path(directory) / "sessions" / recording_id
            self.assertTrue((session / "raw" / "eeg.jsonl").exists())
            self.assertTrue((session / "algorithm" / "attention.jsonl").exists())
            event = store.events(recording_id)[0]
            self.assertEqual(event["timestampMs"], 1200)
            self.assertEqual(event["payload"]["algorithm"]["attention"], 7.0)

    def test_algorithm_metrics_are_written_to_independent_timestamped_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RecordingStore(Path(directory))
            recording_id = store.start()
            eeg_source = {"receivedAtMsStart": 1000, "receivedAtMsEnd": 1010, "packetCount": 2, "windowStartMs": 600, "windowEndMs": 1200}
            hr_source = {"receivedAtMsStart": 1020, "receivedAtMsEnd": 1025, "packetCount": 1, "windowStartMs": 600, "windowEndMs": 1200}
            store.save_algorithm_events(
                algorithm={
                    "eeg": {"quality": 2}, "attention": 58.0, "flow": {"meditation": 22.0},
                    "sleep": {"updated": False}, "hr": {"value": 59, "hrv": 12.3}, "pressure": 4.0,
                },
                computed_at_ms=1030,
                eeg_source=eeg_source,
                hr_source=hr_source,
                valid=True,
                invalid_reasons=[],
            )
            algorithm_dir = Path(directory) / "sessions" / recording_id / "algorithm"
            attention = json.loads((algorithm_dir / "attention.jsonl").read_text(encoding="utf-8"))
            hrv = json.loads((algorithm_dir / "hrv.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(attention["timestampMs"], 1010)
            self.assertEqual(attention["value"], 58.0)
            self.assertEqual(hrv["timestampMs"], 1025)
            self.assertEqual(hrv["algorithm"], {"hr": {"hrv": 12.3}})
            self.assertEqual((algorithm_dir / "sleep.jsonl").read_text(encoding="utf-8"), "")

    def test_export_contains_split_data_files_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            documentation_pdf = Path(directory) / "头环数据采集包格式说明_v0.1.pdf"
            documentation_pdf.write_bytes(b"%PDF-1.4\nCapture package documentation\n%%EOF\n")
            store = RecordingStore(Path(directory), capture_package_pdf=documentation_pdf)
            recording_id = store.start(started_at_ms=1000)
            store.save_raw_packet(stream="eeg", received_at_ms=1100, window_start_ms=600, window_end_ms=1200, value=b"e" * EEG_PACKET_BYTES)
            source = {"receivedAtMsStart": 1100, "receivedAtMsEnd": 1100, "packetCount": 1, "windowStartMs": 600, "windowEndMs": 1200}
            store.save_algorithm_events(algorithm={"attention": 7.0}, computed_at_ms=1120, eeg_source=source, hr_source=None, valid=True, invalid_reasons=[])
            archive = store.export(recording_id)
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                root = f"{recording_id}/"
                self.assertIn(root + "raw/eeg.jsonl", names)
                self.assertIn(root + "raw/hr.jsonl", names)
                self.assertIn(root + "algorithm/attention.jsonl", names)
                self.assertIn(root + documentation_pdf.name, names)
                manifest = json.loads(bundle.read(root + "manifest.json"))
            self.assertEqual(manifest["sessionId"], recording_id)
            self.assertEqual(manifest["formatVersion"], "1.0")
            self.assertEqual(manifest["documentation"]["path"], documentation_pdf.name)
            self.assertEqual(manifest["documentation"]["version"], "0.1")
            self.assertEqual(manifest["documentation"]["sha256"], sha256(documentation_pdf.read_bytes()).hexdigest())


class ReplayLatestTests(unittest.IsolatedAsyncioTestCase):
    async def test_subscribe_confirmation_precedes_replay_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replay_id = "rec-replay"
            gateway = Gateway(config(root, replay_id=replay_id, replay_speed=1))
            gateway.store.recording_id = replay_id
            gateway.store.save_algorithm(timestamp_ms=1_000, valid=True, invalid_reasons=[], algorithm={"attention": 1})
            gateway.store.stop()
            sent: list[dict] = []
            received_data = asyncio.Event()

            async def send(item: dict) -> None:
                sent.append(item)
                if item["data"].get("event") == "data":
                    received_data.set()

            await gateway.handle(ClientSession(), '{"protocolVersion":"1.0","messageType":"request","requestId":"sub-1","action":"subscribe","params":{"streams":["eeg"]}}', send)
            await asyncio.wait_for(received_data.wait(), timeout=1)
            self.assertEqual(sent[0]["data"]["action"], "subscribe")
            self.assertEqual(sent[1]["data"]["event"], "data")
            await gateway.stop()

    async def test_failed_replay_subscriber_does_not_stop_other_subscribers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replay_id = "rec-replay"
            gateway = Gateway(config(root, replay_id=replay_id, replay_speed=10))
            gateway.store.recording_id = replay_id
            for value in (1, 2, 3):
                gateway.store.save_algorithm(timestamp_ms=value * 1_000, valid=True, invalid_reasons=[], algorithm={"attention": value})
            gateway.store.stop()
            received_second = asyncio.Event()
            received: list[int] = []

            async def failed_send(_: dict) -> None:
                raise ConnectionResetError("B-side connection closed")

            async def healthy_send(item: dict) -> None:
                value = item["data"].get("payload", {}).get("algorithm", {}).get("attention")
                if value is not None:
                    received.append(value)
                    if value == 2:
                        received_second.set()

            await gateway.subscribe(ClientSession(), {"streams": ["eeg"]}, failed_send)
            await gateway.subscribe(ClientSession(), {"streams": ["eeg"]}, healthy_send)
            await asyncio.wait_for(received_second.wait(), timeout=1)
            self.assertEqual(received[:2], [1, 2])
            self.assertIsNotNone(gateway._replay_task)
            await gateway.stop()

    async def test_get_latest_starts_gateway_replay_without_a_subscription(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replay_id = "rec-replay"
            gateway = Gateway(config(root, replay_id=replay_id, replay_speed=1))
            gateway.store.recording_id = replay_id
            gateway.store.save_algorithm(timestamp_ms=1_000, valid=True, invalid_reasons=[], algorithm={"attention": 1})
            gateway.store.save_algorithm(timestamp_ms=2_000, valid=True, invalid_reasons=[], algorithm={"attention": 2})
            gateway.store.stop()
            sent: list[dict] = []

            async def send(item: dict) -> None:
                sent.append(item)

            await gateway.handle(ClientSession(), '{"protocolVersion":"1.0","messageType":"request","requestId":"latest-1","action":"getLatest","params":{"streams":["eeg"]}}', send)
            self.assertEqual(sent[0]["data"]["result"]["payload"]["algorithm"]["attention"], 2)
            self.assertIsNotNone(gateway._replay_task)
            await asyncio.sleep(0)
            self.assertEqual(gateway.latest_replay_algorithm()[0], {"attention": 1})
            await gateway.stop()

    async def test_late_subscription_joins_the_single_replay_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replay_id = "rec-replay"
            gateway = Gateway(config(root, replay_id=replay_id, replay_speed=10))
            gateway.store.recording_id = replay_id
            for value in (1, 2, 3):
                gateway.store.save_algorithm(timestamp_ms=value * 1_000, valid=True, invalid_reasons=[], algorithm={"attention": value})
            gateway.store.stop()
            first_started = asyncio.Event()
            second_received = asyncio.Event()
            first_values: list[int] = []
            second_values: list[int] = []

            async def first_send(item: dict) -> None:
                value = item["data"].get("payload", {}).get("algorithm", {}).get("attention")
                if value is not None:
                    first_values.append(value)
                    if value == 1:
                        first_started.set()

            async def second_send(item: dict) -> None:
                value = item["data"].get("payload", {}).get("algorithm", {}).get("attention")
                if value is not None:
                    second_values.append(value)
                    if value == 2:
                        second_received.set()

            await gateway.subscribe(ClientSession(), {"streams": ["eeg"]}, first_send)
            await asyncio.wait_for(first_started.wait(), timeout=1)
            await gateway.subscribe(ClientSession(), {"streams": ["eeg"]}, second_send)
            await asyncio.wait_for(second_received.wait(), timeout=1)
            self.assertEqual(first_values[:2], [1, 2])
            self.assertEqual(second_values[:1], [2])
            await gateway.stop()

    async def test_last_b_side_disconnection_stops_replay_and_resets_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replay_id = "rec-replay"
            gateway = Gateway(config(root, replay_id=replay_id, replay_speed=1))
            gateway.store.recording_id = replay_id
            gateway.store.save_algorithm(timestamp_ms=1_000, valid=True, invalid_reasons=[], algorithm={"attention": 1})
            gateway.store.save_algorithm(timestamp_ms=2_000, valid=True, invalid_reasons=[], algorithm={"attention": 2})
            gateway.store.stop()
            first_received = asyncio.Event()

            async def send(item: dict) -> None:
                if item["data"].get("payload", {}).get("algorithm", {}).get("attention") == 1:
                    first_received.set()

            session = ClientSession()
            await gateway.subscribe(session, {"streams": ["eeg"]}, send)
            await asyncio.wait_for(first_received.wait(), timeout=1)
            await gateway.close_session(session)

            self.assertFalse(gateway.sessions)
            self.assertIsNone(gateway._replay_task)
            self.assertIsNone(gateway._replay_algorithm)
            self.assertIsNone(gateway._replay_algorithm_timestamp)

    async def test_connected_b_side_restarts_replay_from_the_first_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replay_id = "rec-replay"
            gateway = Gateway(config(root, replay_id=replay_id, replay_speed=1_000))
            gateway.store.recording_id = replay_id
            for value in (1, 2):
                gateway.store.save_algorithm(timestamp_ms=value * 1_000, valid=True, invalid_reasons=[], algorithm={"attention": value})
            gateway.store.stop()
            restarted = asyncio.Event()
            values: list[int] = []

            async def send(item: dict) -> None:
                value = item["data"].get("payload", {}).get("algorithm", {}).get("attention")
                if value is not None:
                    values.append(value)
                    if values[:3] == [1, 2, 1]:
                        restarted.set()

            session = ClientSession()
            await gateway.subscribe(session, {"streams": ["eeg"]}, send)
            await asyncio.wait_for(restarted.wait(), timeout=1)

            self.assertIn(session, gateway.sessions)
            self.assertIsNotNone(gateway._replay_task)
            await gateway.close_session(session)
            await gateway.stop()

    async def test_device_connection_stops_the_active_gateway_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replay_id = "rec-replay"
            gateway = Gateway(config(root, replay_id=replay_id, replay_speed=1))
            gateway.store.recording_id = replay_id
            gateway.store.save_algorithm(timestamp_ms=1_000, valid=True, invalid_reasons=[], algorithm={"attention": 1})
            gateway.store.save_algorithm(timestamp_ms=2_000, valid=True, invalid_reasons=[], algorithm={"attention": 2})
            gateway.store.stop()

            async def ignored_send(_: dict) -> None:
                pass

            await gateway.subscribe(ClientSession(), {"streams": ["eeg"]}, ignored_send)
            await asyncio.sleep(0)
            self.assertIsNotNone(gateway._replay_task)
            await gateway.update_status("connectionState", "connected")
            self.assertIsNone(gateway._replay_task)
            await gateway.stop()

    async def test_get_latest_uses_latest_valid_replay_result_without_subscription(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replay_id = "rec-replay"
            gateway = Gateway(config(root, replay_id=replay_id))
            gateway.store.recording_id = replay_id
            gateway.store.save_algorithm(timestamp_ms=10_000, valid=True, invalid_reasons=[], algorithm={"attention": 1})
            gateway.store.save_algorithm(timestamp_ms=20_000, valid=False, invalid_reasons=["ALGORITHM_ERROR"], algorithm={"attention": 2})
            gateway.store.stop()
            latest = gateway.get_latest(ClientSession(), {"streams": ["eeg"]})
            self.assertTrue(latest["valid"])
            self.assertEqual(latest["timestampMs"], 10_000)
            self.assertEqual(latest["payload"]["algorithm"]["attention"], 1)

    async def test_get_latest_follows_replay_cursor_not_recording_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replay_id = "rec-replay"
            gateway = Gateway(config(root, replay_id=replay_id))
            gateway.store.recording_id = replay_id
            for value in range(1, 9):
                gateway.store.save_algorithm(timestamp_ms=value * 10_000, valid=True, invalid_reasons=[], algorithm={"attention": value})
            gateway.store.stop()
            session = ClientSession()
            reached_four = asyncio.Event()
            observed: list[int] = []

            async def send(item: dict) -> None:
                payload = item["data"].get("payload", {})
                if payload.get("algorithm", {}).get("attention") == 4:
                    latest = gateway.get_latest(session, {"streams": ["eeg"]})
                    observed.append(latest["payload"]["algorithm"]["attention"])
                    reached_four.set()

            await gateway.subscribe(session, {"streams": ["eeg"]}, send)
            await asyncio.wait_for(reached_four.wait(), timeout=1)
            self.assertEqual(observed, [4])
            await gateway.close_session(session)


class ErrorContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_unavailable_uses_the_locked_error_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gateway = Gateway(config(Path(directory)))
            sent: list[dict] = []
            async def send(item: dict) -> None:
                sent.append(item)
            await gateway.handle(ClientSession(), '{"protocolVersion":"1.0","messageType":"request","requestId":"latest-1","action":"getLatest","params":{"streams":["eeg"]}}', send)
            self.assertEqual(sent[0]["code"], 503)
            self.assertEqual(sent[0]["data"]["reason"], REPLAY_NOT_AVAILABLE_REASON)
