from __future__ import annotations

import asyncio
import base64
from pathlib import Path
import tempfile
import unittest

from neurobridge.config import AlgorithmConfig, BleConfig, GatewayConfig, RecordingConfig, ServerConfig
from neurobridge.gateway import ClientSession, Gateway
from neurobridge.packets import DataWindow, EEG_PACKET_BYTES, HR_RAW_PACKET_BYTES, RawPacket, WindowAssembler
from neurobridge.recording import RecordingStore


def config(root: Path, replay_id: str | None = None) -> GatewayConfig:
    return GatewayConfig(ServerConfig("127.0.0.1", 8765, "/neurobridge/v1/ws"), BleConfig(False, "", None, 5, 3), RecordingConfig(root, "SUBJECT-001", replay_id, 1000), AlgorithmConfig(False, ()))


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
