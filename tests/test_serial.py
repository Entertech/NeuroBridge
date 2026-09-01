from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from neurobridge.config import SerialConfig
from neurobridge.device.packet import DevicePacket
from neurobridge.serial.adapter import (
    FRAME_HEADER,
    FRAME_TAIL,
    HANDSHAKE,
    START_COMMAND,
    SequenceLossTracker,
    SerialAdapter,
    _open_serial,
    _serial_discovery_inventory,
    _serial_candidate_order_key,
)


class FakeSerial:
    def __init__(self, reads: list[bytes]) -> None:
        self.reads = list(reads)
        self.writes: list[bytes] = []
        self.timeout = 0.1
        self.closed = False

    def read(self, _size: int) -> bytes:
        return self.reads.pop(0) if self.reads else b""

    def write(self, value: bytes) -> int:
        self.writes.append(bytes(value))
        return len(value)

    def flush(self) -> None:
        return None

    def reset_input_buffer(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class GarbageSerial(FakeSerial):
    def __init__(self) -> None:
        super().__init__([])

    def read(self, _size: int) -> bytes:
        return b"garbage"


class SlowEmptySerial(FakeSerial):
    def read(self, size: int) -> bytes:
        if self.reads:
            return super().read(size)
        time.sleep(0.01)
        return b""


def frame(sequence: int, fill: int = 1, heart_rate: int = 60) -> bytes:
    return (
        FRAME_HEADER
        + bytes([28])
        + sequence.to_bytes(2, "big")
        + bytes([fill]) * 18
        + bytes([heart_rate])
        + FRAME_TAIL
    )


async def noop(*_args) -> None:
    return None


class SequenceLossTrackerTests(unittest.TestCase):
    def test_rfc_style_loss_handles_gap_recovery_duplicate_and_wrap(self) -> None:
        tracker = SequenceLossTracker()
        self.assertEqual(tracker.observe(0xFFFE).classification, "baseline")
        self.assertEqual(tracker.observe(0xFFFF).classification, "in_order")
        self.assertEqual(tracker.observe(0x0000).classification, "in_order")
        gap = tracker.observe(0x0002)
        self.assertEqual(gap.classification, "gap")
        self.assertEqual(gap.expected_sequence, 1)
        self.assertEqual(gap.gap_packets, 1)
        self.assertEqual(gap.snapshot.expected_packets, 5)
        self.assertEqual(gap.snapshot.received_unique_packets, 4)
        self.assertEqual(gap.snapshot.lost_packets, 1)
        self.assertAlmostEqual(gap.snapshot.loss_rate_percent, 20.0)
        recovered = tracker.observe(0x0001)
        self.assertEqual(recovered.classification, "out_of_order")
        self.assertEqual(recovered.snapshot.lost_packets, 0)
        duplicate = tracker.observe(0x0001)
        self.assertEqual(duplicate.classification, "duplicate")
        self.assertEqual(duplicate.snapshot.received_unique_packets, 5)
        self.assertEqual(duplicate.snapshot.duplicate_packets, 1)


class SerialAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_inventory_explains_missing_nodes_and_fixed_path(self) -> None:
        entries = {
            "/dev/serial/by-id/*": ["/dev/serial/by-id/a"],
            "/dev/ttyACM*": ["/dev/ttyACM0", "/dev/ttyACM1"],
            "/dev/ttyUSB*": [],
        }
        with patch("neurobridge.serial.adapter.glob", side_effect=lambda pattern: entries[pattern]):
            inventory = _serial_discovery_inventory(SerialConfig(device="/dev/not-present"))
        self.assertEqual(inventory["byIdEntries"], 1)
        self.assertEqual(inventory["ttyACMEntries"], 2)
        self.assertEqual(inventory["ttyUSBEntries"], 0)
        self.assertFalse(inventory["configuredPathExists"])

    async def test_zero_candidates_log_each_device_node_class_without_payload(self) -> None:
        adapter: SerialAdapter

        async def stop_after_error(_reason: str) -> None:
            await adapter.stop()

        adapter = SerialAdapter(
            SerialConfig(device="auto"),
            noop,
            noop,
            noop,
            error=stop_after_error,
            candidate_provider=lambda _config: [],
        )
        entries = {
            "/dev/serial/by-id/*": [],
            "/dev/ttyACM*": [],
            "/dev/ttyUSB*": [],
        }
        with (
            patch("neurobridge.serial.adapter.glob", side_effect=lambda pattern: entries[pattern]),
            self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs,
        ):
            await adapter.run()
        rendered = "\n".join(logs.output)
        self.assertIn("Serial discovery found no usable candidates", rendered)
        self.assertIn("byIdEntries=0", rendered)
        self.assertIn("ttyACMEntries=0", rendered)
        self.assertIn("ttyUSBEntries=0", rendered)
        self.assertIn("configuredPathExists=not_applicable", rendered)

    async def test_candidate_order_prefers_by_id_then_usb_parent_and_interface(self) -> None:
        first = _serial_candidate_order_key(
            "/dev/serial/by-id/z",
            {"usbParent": "/sys/usb/2", "interface": "01", "physicalPath": "/sys/usb/2/2-1:1.1"},
        )
        second = _serial_candidate_order_key(
            "/dev/ttyACM0",
            {"usbParent": "/sys/usb/1", "interface": "00", "physicalPath": "/sys/usb/1/1-1:1.0"},
        )
        self.assertLess(first, second)
        interface_zero = _serial_candidate_order_key(
            "/dev/ttyACM1",
            {"usbParent": "/sys/usb/1", "interface": "00", "physicalPath": "/sys/usb/1/1-1:1.0"},
        )
        interface_one = _serial_candidate_order_key(
            "/dev/ttyACM2",
            {"usbParent": "/sys/usb/1", "interface": "01", "physicalPath": "/sys/usb/1/1-1:1.1"},
        )
        self.assertLess(interface_zero, interface_one)

    async def test_modem_control_lines_are_set_before_serial_open(self) -> None:
        class Client:
            def __init__(self, **_kwargs) -> None:
                self.dtr = True
                self.rts = True
                self.port = None
                self.open_snapshot: tuple[bool, bool, str | None] | None = None

            def open(self) -> None:
                self.open_snapshot = (self.dtr, self.rts, self.port)

            def close(self) -> None:
                pass

        client = Client()
        fake_serial = SimpleNamespace(
            EIGHTBITS=8,
            PARITY_NONE="N",
            STOPBITS_ONE=1,
            Serial=lambda **_kwargs: client,
        )
        with patch.dict(sys.modules, {"serial": fake_serial}):
            opened = _open_serial("/dev/ttyACM-test", SerialConfig(dtr=False, rts=False))
        self.assertIs(opened, client)
        self.assertEqual(client.open_snapshot, (False, False, "/dev/ttyACM-test"))

    async def test_fixed_handshake_is_acked_and_stops_candidate_probe(self) -> None:
        client = FakeSerial([b"ignored" + HANDSHAKE])
        adapter = SerialAdapter(SerialConfig(), noop, noop, noop)
        accepted = await adapter._await_handshake(client, "/dev/ttyACM0", 1, 1, 3)
        self.assertTrue(accepted)
        self.assertEqual(client.writes, [HANDSHAKE])

    async def test_wrong_handshake_is_explicitly_logged_without_payload(self) -> None:
        wrong_handshake = bytes.fromhex("AA 55 01 01 01 01 70")
        client = SlowEmptySerial([wrong_handshake])
        adapter = SerialAdapter(SerialConfig(handshake_timeout_ms=200), noop, noop, noop)
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            accepted = await adapter._await_handshake(client, "/dev/ttyACM0", 1, 1, 1)
        self.assertFalse(accepted)
        rendered = "\n".join(logs.output)
        self.assertIn("produced bytes but no valid handshake", rendered)
        self.assertIn("invalidHandshakeObserved=true", rendered)
        self.assertIn("longestHandshakePrefixBytes=6", rendered)
        self.assertIn("payloadLogged=false", rendered)
        self.assertNotIn(wrong_handshake.hex(), rendered)

    async def test_no_handshake_bytes_is_distinguished_from_wrong_handshake(self) -> None:
        client = SlowEmptySerial([])
        adapter = SerialAdapter(SerialConfig(handshake_timeout_ms=200), noop, noop, noop)
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            accepted = await adapter._await_handshake(client, "/dev/ttyACM0", 1, 1, 1)
        self.assertFalse(accepted)
        rendered = "\n".join(logs.output)
        self.assertIn("produced no handshake bytes", rendered)
        self.assertNotIn("invalidHandshakeObserved=true", rendered)

    async def test_any_nonempty_control_response_is_success_without_logging_payload(self) -> None:
        client = FakeSerial([b"arbitrary-response"])
        adapter = SerialAdapter(SerialConfig(), noop, noop, noop)
        adapter._client = client
        with self.assertLogs("neurobridge.serial.adapter", level="INFO") as logs:
            response = await adapter._send_control(START_COMMAND, "start")
        self.assertEqual(response, b"arbitrary-response")
        self.assertEqual(client.writes, [START_COMMAND])
        rendered = "\n".join(logs.output)
        self.assertIn("responseBytes=18", rendered)
        self.assertNotIn("arbitrary-response", rendered)

    async def test_empty_control_response_is_a_logged_timeout(self) -> None:
        client = FakeSerial([b""])
        adapter = SerialAdapter(SerialConfig(command_response_timeout_ms=250), noop, noop, noop)
        adapter._client = client
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            response = await adapter._send_control(START_COMMAND, "start")
        self.assertEqual(response, b"")
        self.assertIn("timeoutMs=250", "\n".join(logs.output))
        self.assertIn("success=false", "\n".join(logs.output))

    async def test_frames_map_to_existing_raw_channels_and_log_industry_loss(self) -> None:
        packets: list[tuple[str, bytes]] = []

        async def packet(event: DevicePacket) -> None:
            packets.append((event.channel, event.value))

        adapter = SerialAdapter(SerialConfig(), packet, noop, noop)
        buffer = bytearray(b"noise" + frame(10, 2, 61) + frame(12, 3, 62))
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            parsed = await adapter._consume_frames(buffer)
        self.assertEqual(parsed, 2)
        self.assertEqual(
            [channel for channel, _value in packets],
            ["serial.frame", "ff31", "ff51", "serial.frame", "ff31", "ff51"],
        )
        self.assertEqual(len(packets[0][1]), 28)
        self.assertEqual(len(packets[1][1]), 20)
        self.assertEqual(packets[1][1][:2], b"\x00\x0a")
        self.assertEqual(packets[2][1], bytes([61]))
        snapshot = adapter._loss.snapshot()
        self.assertEqual(snapshot.expected_packets, 3)
        self.assertEqual(snapshot.received_unique_packets, 2)
        self.assertEqual(snapshot.lost_packets, 1)
        self.assertAlmostEqual(snapshot.loss_rate_percent, 100 / 3)
        self.assertIn("expectedSequence=11", "\n".join(logs.output))
        self.assertIn("payloadLogged=false", "\n".join(logs.output))
        self.assertEqual(buffer, b"")
        with self.assertLogs("neurobridge.serial.adapter", level="INFO") as summaries:
            adapter._log_stats("periodic")
        summary = "\n".join(summaries.output)
        self.assertIn("intervalExpectedPackets=3", summary)
        self.assertIn("intervalReceivedUniquePackets=2", summary)
        self.assertIn("intervalLostPackets=1", summary)
        self.assertIn("intervalLossRatePercent=33.333333", summary)

    async def test_invalid_length_and_tail_are_logged_without_frame_payload(self) -> None:
        adapter = SerialAdapter(SerialConfig(), noop, noop, noop)
        invalid_length = FRAME_HEADER + b"\x1d" + bytes([0xA5]) * 24
        invalid_tail = frame(1)[:-1] + b"\xBC"
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            await adapter._consume_frames(bytearray(invalid_length))
            await adapter._consume_frames(bytearray(invalid_tail))
        rendered = "\n".join(logs.output)
        self.assertIn("Serial invalid frame rejected: reason=length", rendered)
        self.assertIn("Serial invalid frame rejected: reason=tail", rendered)
        self.assertIn("payloadLogged=false", rendered)
        self.assertNotIn(invalid_length.hex(), rendered)
        self.assertNotIn(invalid_tail.hex(), rendered)

    async def test_continuous_invalid_bytes_still_trigger_valid_frame_timeout(self) -> None:
        adapter = SerialAdapter(SerialConfig(data_timeout_seconds=0.01), noop, noop, noop)
        adapter._client = GarbageSerial()
        with self.assertRaisesRegex(TimeoutError, "No valid serial data frame"):
            await adapter._stream(b"")

    async def test_concurrent_cleanup_sends_stop_command_only_once(self) -> None:
        client = FakeSerial([b"ok"])
        adapter = SerialAdapter(SerialConfig(), noop, noop, noop)
        adapter._client = client
        adapter._capture_started = True
        await asyncio.gather(
            adapter._send_stop_best_effort("service_stop"),
            adapter._send_stop_best_effort("adapter_cleanup"),
        )
        self.assertEqual(client.writes, [b"\xE0"])

    async def test_full_adapter_lifecycle_uses_handshake_control_stream_and_stop(self) -> None:
        client = FakeSerial([HANDSHAKE, b"start-ok", frame(7, 4, 63), b"stop-ok"])
        packets: list[tuple[str, bytes]] = []
        states: list[tuple[str, object]] = []
        adapter: SerialAdapter

        async def packet(event: DevicePacket) -> None:
            packets.append((event.channel, event.value))
            if event.channel == "ff31":
                await adapter.stop()

        async def status(name: str, value: object) -> None:
            states.append((name, value))

        adapter = SerialAdapter(
            SerialConfig(),
            packet,
            status,
            noop,
            candidate_provider=lambda _config: ["/dev/ttyACM-test"],
            serial_factory=lambda _path, _config: client,
        )
        await adapter.run()
        self.assertEqual(client.writes, [HANDSHAKE, b"\xE1", b"\xE0"])
        self.assertEqual([channel for channel, _value in packets], ["serial.frame", "ff31", "ff51"])
        self.assertIn(("connectionState", "connected"), states)
        self.assertEqual(states[-1], ("connectionState", "disconnected"))
        self.assertTrue(client.closed)
