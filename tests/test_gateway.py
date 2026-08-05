from __future__ import annotations

import asyncio
import base64
from pathlib import Path
import tempfile
import unittest

from neurobridge.config import AlgorithmConfig, BleConfig, GatewayConfig, RecordingConfig, ServerConfig
from neurobridge.ble.flowtime import FlowtimeAdapter
from neurobridge.business.gateway import ClientSession, Gateway
from neurobridge.ble.packets import DataWindow, EEG_PACKET_BYTES, HR_RAW_PACKET_BYTES, RawPacket, WindowAssembler
from neurobridge.business.recording import RecordingStore


def config(root: Path, replay_id: str | None = None) -> GatewayConfig:
    return GatewayConfig(ServerConfig("127.0.0.1", 8765, "/neurobridge/v1/ws"), BleConfig(False, "Flowtime Headband", "0000ff10-1212-abcd-1523-785feabcd123", 5, 3), RecordingConfig(root, "SUBJECT-001", replay_id, 1000), AlgorithmConfig(False, ()))


class PacketTests(unittest.TestCase):
    def test_window_keeps_original_bytes_and_counts(self) -> None:
        assembler = WindowAssembler()
        assembler.add("ff31", b"a" * EEG_PACKET_BYTES, 1000)
        assembler.add("ff52", b"b" * HR_RAW_PACKET_BYTES, 1010)
        windows = assembler.add("ff31", b"c" * EEG_PACKET_BYTES, 1601)
        self.assertEqual(len(windows), 1)
        payload = windows[0].raw_payload()
        self.assertEqual(payload["eegRaw"]["packetCount"], 1)
        self.assertEqual(base64.b64decode(payload["eegRaw"]["bytesBase64"]), b"a" * EEG_PACKET_BYTES)
        self.assertEqual(payload["hrRaw"]["packetCount"], 1)

    def test_native_hr_is_persisted_but_not_a_northbound_stream(self) -> None:
        assembler = WindowAssembler()
        assembler.add("ff51", b"n" * 16, 1000)
        window = assembler.add("ff31", b"e" * 14, 1601)[0]
        self.assertIn("nativeHr", window.raw_payload())
        self.assertEqual(window.raw_payload()["nativeHr"]["packetBytes"], 16)


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
            self.assertEqual(event["data"]["payload"]["eegRaw"]["packetBytes"], 14)
            await gateway.close_session(session)

    async def test_invalid_request_is_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gateway = Gateway(config(Path(directory)))
            sent: list[dict] = []
            async def send(item: dict) -> None:
                sent.append(item)
            await gateway.handle(ClientSession(), '{"protocolVersion":"2.0"}', send)
            self.assertEqual(sent[0]["code"], 400)
            self.assertEqual(sent[0]["data"]["reason"], "INVALID_REQUEST")

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
    def test_raw_and_algorithm_are_separate_then_replay_merges_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RecordingStore(Path(directory))
            recording_id = store.start()
            store.save_raw(timestamp_ms=1200, valid=True, invalid_reasons=[], payload={"eegRaw": {"packetBytes": 14}})
            store.save_algorithm(timestamp_ms=1200, valid=True, invalid_reasons=[], algorithm={"attention": 7.0})
            self.assertTrue((Path(directory) / "raw" / f"{recording_id}.jsonl").exists())
            self.assertTrue((Path(directory) / "algorithm" / f"{recording_id}.jsonl").exists())
            event = store.events(recording_id)[0]
            self.assertEqual(event["timestampMs"], 1200)
            self.assertEqual(event["payload"]["algorithm"]["attention"], 7.0)


class ReplayLatestTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_latest_is_invalid_before_this_session_has_replay_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replay_id = "rec-replay"
            gateway = Gateway(config(root, replay_id=replay_id))
            gateway.store.recording_id = replay_id
            gateway.store.save_algorithm(timestamp_ms=10_000, valid=True, invalid_reasons=[], algorithm={"attention": 1})
            gateway.store.stop()
            latest = gateway.get_latest(ClientSession(), {"streams": ["eeg"]})
            self.assertFalse(latest["valid"])
            self.assertEqual(latest["payload"], {})

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
