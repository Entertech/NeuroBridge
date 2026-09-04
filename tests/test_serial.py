from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile
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
    discover_serial_candidates,
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

    def read(self, size: int) -> bytes:
        if not self.reads:
            return b""
        value = self.reads.pop(0)
        if len(value) > size:
            self.reads.insert(0, value[size:])
        return value[:size]

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


class AckAndStartDrivenSerial(SlowEmptySerial):
    def __init__(self, started_frame: bytes | None) -> None:
        super().__init__([])
        self.started_frame = started_frame

    def write(self, value: bytes) -> int:
        written = super().write(value)
        if value == HANDSHAKE:
            self.reads.append(b"\x01")
        elif value == START_COMMAND and self.started_frame is not None:
            self.reads.append(self.started_frame)
            self.started_frame = None
        return written


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


async def ready() -> bool:
    return True


class SerialDiscoveryTests(unittest.TestCase):
    def test_fixed_path_rejects_non_usb_tty_character_devices(self) -> None:
        for path in ("/dev/null", "/dev/tty"):
            with self.subTest(path=path):
                self.assertEqual(discover_serial_candidates(SerialConfig(device=path)), [])

    def test_fixed_path_disappearing_before_discovery_returns_no_candidate(self) -> None:
        self.assertEqual(
            discover_serial_candidates(SerialConfig(device="/dev/ttyUSB999999")),
            [],
        )

    def test_fixed_path_rejects_regular_file_named_like_usb_tty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ttyUSB0"
            path.write_bytes(b"")
            self.assertEqual(discover_serial_candidates(SerialConfig(device=str(path))), [])


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
    @staticmethod
    def stable_identity(usb_serial: str = "EARPHONE-001") -> dict[str, str | None]:
        return {
            "resolvedPath": "/dev/ttyUSB0",
            "vid": "1234",
            "pid": "5678",
            "usbSerial": usb_serial,
            "interface": "00",
            "driver": "ftdi_sio",
            "usbParent": "/sys/devices/pci/usb1/1-1",
            "physicalPath": "/sys/devices/pci/usb1/1-1/1-1:1.0/ttyUSB0",
        }

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

    async def test_active_ack_probe_does_not_wait_for_device_handshake(self) -> None:
        client = FakeSerial([b"\x01"])
        adapter = SerialAdapter(SerialConfig(), noop, noop, noop)
        response = await adapter._send_handshake_ack(client)
        self.assertEqual(response, b"\x01")
        self.assertEqual(client.writes, [HANDSHAKE])

    async def test_active_ack_probe_rejects_non_01_without_logging_payload(self) -> None:
        unexpected = b"bad"
        client = SlowEmptySerial([unexpected])
        adapter = SerialAdapter(SerialConfig(command_response_timeout_ms=30), noop, noop, noop)
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            response = await adapter._send_handshake_ack(client)
        self.assertEqual(response, b"")
        rendered = "\n".join(logs.output)
        self.assertIn("handshake ACK response timed out", rendered)
        self.assertIn("unexpectedBytes=3", rendered)
        self.assertIn("payloadLogged=false", rendered)
        self.assertNotIn("bad", rendered)

    async def test_active_ack_probe_timeout_is_explicit(self) -> None:
        client = SlowEmptySerial([])
        adapter = SerialAdapter(SerialConfig(command_response_timeout_ms=30), noop, noop, noop)
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            response = await adapter._send_handshake_ack(client)
        self.assertEqual(response, b"")
        rendered = "\n".join(logs.output)
        self.assertIn("timeoutMs=30", rendered)
        self.assertIn("totalReadBytes=0", rendered)

    async def test_open_failure_preserves_permission_root_cause(self) -> None:
        reasons: list[str] = []
        adapter: SerialAdapter

        async def stop_after_error(reason: str) -> None:
            reasons.append(reason)
            await adapter.stop()

        def deny_open(_path: str, _config: SerialConfig) -> FakeSerial:
            raise PermissionError(13, "Permission denied", "/dev/ttyUSB0")

        adapter = SerialAdapter(
            SerialConfig(),
            noop,
            noop,
            noop,
            error=stop_after_error,
            candidate_provider=lambda _config: ["/dev/ttyUSB0"],
            serial_factory=deny_open,
        )
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            await adapter.run()

        self.assertEqual(len(reasons), 1)
        self.assertIn("Unable to open any usable serial candidate", reasons[0])
        self.assertIn("lastErrorType=PermissionError", reasons[0])
        self.assertIn("Permission denied", reasons[0])
        rendered = "\n".join(logs.output)
        self.assertIn("phase=candidate_open", rendered)
        self.assertNotIn("passed the active handshake ACK probe", rendered)

    async def test_non_01_handshake_ack_response_is_rejected_without_logging_payload(self) -> None:
        client = SlowEmptySerial([b"bad"])
        adapter = SerialAdapter(SerialConfig(command_response_timeout_ms=30), noop, noop, noop)
        adapter._client = client
        with self.assertLogs("neurobridge.serial.adapter", level="INFO") as logs:
            response = await adapter._send_handshake_ack()
        self.assertEqual(response, b"")
        self.assertEqual(client.writes, [HANDSHAKE])
        rendered = "\n".join(logs.output)
        self.assertIn("totalReadBytes=3", rendered)
        self.assertIn("unexpectedBytes=3", rendered)
        self.assertIn("expectedAck01=true", rendered)
        self.assertIn("payloadLogged=false", rendered)
        self.assertIn("success=false", rendered)
        self.assertNotIn("bad", rendered)

    async def test_single_byte_01_handshake_ack_response_is_visible_without_raw_payload(self) -> None:
        client = FakeSerial([b"\x01"])
        adapter = SerialAdapter(SerialConfig(), noop, noop, noop)
        adapter._client = client
        with self.assertLogs("neurobridge.serial.adapter", level="INFO") as logs:
            response = await adapter._send_handshake_ack()
        self.assertEqual(response, b"\x01")
        rendered = "\n".join(logs.output)
        self.assertIn("responseClassification=single_byte_0x01", rendered)
        self.assertIn("expectedAck01=true", rendered)
        self.assertIn("singleByteHex=01", rendered)
        self.assertIn("ackWriteCount=1", rendered)
        self.assertIn("ackWriteBytes=7", rendered)
        self.assertIn("success=true", rendered)

    async def test_repeated_handshake_is_reacked_while_waiting_for_standalone_01(self) -> None:
        client = FakeSerial([HANDSHAKE, b"\x01"])
        adapter = SerialAdapter(SerialConfig(), noop, noop, noop)
        adapter._client = client
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            response = await adapter._send_handshake_ack()
        self.assertEqual(response, b"\x01")
        self.assertEqual(client.writes, [HANDSHAKE, HANDSHAKE])
        rendered = "\n".join(logs.output)
        self.assertIn("repeatedHandshakeFrames=1", rendered)
        self.assertIn("ackWriteCount=2", rendered)
        self.assertIn("ackWriteBytes=14", rendered)
        self.assertIn("action=ack_resent", rendered)
        self.assertNotIn(HANDSHAKE.hex(), rendered.lower())
        with self.assertLogs("neurobridge.serial.adapter", level="INFO") as summaries:
            adapter._log_stats("test")
        summary = "\n".join(summaries.output)
        self.assertIn("handshakeAckWrites=2", summary)
        self.assertIn("handshakeAckWriteBytes=14", summary)
        self.assertIn("handshakeAckRepeatedFrames=1", summary)

    async def test_01_inside_repeated_handshake_is_not_misclassified_as_ack_result(self) -> None:
        client = SlowEmptySerial([HANDSHAKE])
        adapter = SerialAdapter(SerialConfig(command_response_timeout_ms=30), noop, noop, noop)
        adapter._client = client
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            response = await adapter._send_handshake_ack()
        self.assertEqual(response, b"")
        self.assertEqual(client.writes, [HANDSHAKE, HANDSHAKE])
        rendered = "\n".join(logs.output)
        self.assertIn("repeatedHandshakeFrames=1", rendered)
        self.assertIn("success=false", rendered)
        self.assertNotIn("responseClassification=single_byte_0x01", rendered)

    async def test_empty_handshake_ack_response_is_a_logged_timeout(self) -> None:
        client = SlowEmptySerial([b""])
        adapter = SerialAdapter(SerialConfig(command_response_timeout_ms=30), noop, noop, noop)
        adapter._client = client
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            response = await adapter._send_handshake_ack()
        self.assertEqual(response, b"")
        self.assertIn("timeoutMs=30", "\n".join(logs.output))
        self.assertIn("success=false", "\n".join(logs.output))

    async def test_e1_is_write_only_and_does_not_consume_stream_data(self) -> None:
        pending_frame = frame(9, 3, 64)
        client = FakeSerial([pending_frame])
        adapter = SerialAdapter(SerialConfig(), noop, noop, noop)
        adapter._client = client
        with self.assertLogs("neurobridge.serial.adapter", level="INFO") as logs:
            await adapter._send_command(START_COMMAND, "start")
        self.assertEqual(client.writes, [START_COMMAND])
        self.assertEqual(client.reads, [pending_frame])
        rendered = "\n".join(logs.output)
        self.assertIn("command=start", rendered)
        self.assertIn("responseExpected=false", rendered)
        self.assertIn("success=true", rendered)

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

    async def test_fixed_handshake_during_stream_is_counted_without_payload(self) -> None:
        adapter = SerialAdapter(SerialConfig(), noop, noop, noop)
        buffer = bytearray(HANDSHAKE + HANDSHAKE)
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            await adapter._consume_frames(buffer)
        rendered = "\n".join(logs.output)
        self.assertIn("Serial fixed handshake observed during data stream", rendered)
        self.assertIn("streamHandshakeFrames=2", rendered)
        self.assertIn("payloadLogged=false", rendered)
        self.assertNotIn(HANDSHAKE.hex(), rendered.lower())
        with self.assertLogs("neurobridge.serial.adapter", level="INFO") as summaries:
            adapter._log_stats("test")
        self.assertIn("streamHandshakeFrames=2", "\n".join(summaries.output))

    async def test_existing_stream_preserves_serial_read_boundary_timestamp(self) -> None:
        packets: list[DevicePacket] = []
        existing_frame = frame(10, 2, 61)

        async def packet(event: DevicePacket) -> None:
            packets.append(event)

        adapter = SerialAdapter(SerialConfig(), packet, noop, noop)

        with patch("neurobridge.serial.adapter.wall_clock_ms", return_value=123456789):
            observed = await adapter._observe_existing_stream(FakeSerial([existing_frame]), "/dev/ttyUSB0")

        self.assertEqual(observed, (existing_frame, 123456789))
        assert observed is not None
        buffered, received_at_ms = observed
        await adapter._consume_frames(bytearray(buffered), received_at_ms)
        self.assertEqual([packet.received_at_ms for packet in packets], [123456789] * 3)

    async def test_handshake_pattern_inside_valid_frame_is_not_counted(self) -> None:
        adapter = SerialAdapter(SerialConfig(), noop, noop, noop)
        valid = bytearray(frame(1))
        valid[7:14] = HANDSHAKE
        parsed = await adapter._consume_frames(valid)
        self.assertEqual(parsed, 1)
        self.assertEqual(adapter._stats["streamHandshakeFrames"], 0)

    async def test_continuous_invalid_bytes_still_trigger_valid_frame_timeout(self) -> None:
        adapter = SerialAdapter(SerialConfig(data_timeout_seconds=0.01), noop, noop, noop)
        adapter._client = GarbageSerial()
        with self.assertRaisesRegex(TimeoutError, "No valid serial data frame"):
            await adapter._stream(b"")

    async def test_concurrent_cleanup_sends_stop_command_only_once(self) -> None:
        client = FakeSerial([])
        adapter = SerialAdapter(SerialConfig(), noop, noop, noop)
        adapter._client = client
        adapter._capture_started = True
        await asyncio.gather(
            adapter._send_stop_best_effort("service_stop"),
            adapter._send_stop_best_effort("adapter_cleanup"),
        )
        self.assertEqual(client.writes, [b"\xE0"])

    async def test_full_adapter_lifecycle_uses_handshake_control_stream_and_stop(self) -> None:
        client = AckAndStartDrivenSerial(frame(7, 4, 63))
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
            SerialConfig(handshake_timeout_ms=10, command_response_timeout_ms=30),
            packet,
            status,
            ready,
            candidate_provider=lambda _config: ["/dev/ttyACM-test"],
            serial_factory=lambda _path, _config: client,
        )
        await adapter.run()
        self.assertEqual(client.writes, [HANDSHAKE, b"\xE1", b"\xE0"])
        self.assertEqual([channel for channel, _value in packets], ["serial.frame", "ff31", "ff51"])
        self.assertEqual(
            states,
            [
                ("connectionState", "connecting"),
                ("connectionState", "validating"),
                ("connectionState", "validated"),
                ("connectionState", "disconnected"),
            ],
        )
        self.assertEqual(states[-1], ("connectionState", "disconnected"))
        self.assertTrue(client.closed)

    async def test_process_restart_repeats_active_ack_and_e1(self) -> None:
        config = SerialConfig(
            handshake_timeout_ms=10,
            command_response_timeout_ms=30,
            data_timeout_seconds=0.1,
        )
        for sequence in (7, 8):
            client = AckAndStartDrivenSerial(frame(sequence, 4, 63))
            adapter: SerialAdapter

            async def stop_after_frame(event: DevicePacket) -> None:
                if event.channel == "ff31":
                    await adapter.stop()

            adapter = SerialAdapter(
                config,
                stop_after_frame,
                noop,
                ready,
                candidate_provider=lambda _config: ["/dev/ttyUSB0"],
                serial_factory=lambda _path, _config: client,
            )
            await adapter.run()
            self.assertEqual(client.writes, [HANDSHAKE, START_COMMAND, b"\xE0"])

    async def test_existing_valid_frame_is_validated_without_ack_or_e1(self) -> None:
        client = FakeSerial([frame(21, 6, 65)])
        states: list[tuple[str, object]] = []
        adapter: SerialAdapter

        async def receive_and_stop(event: DevicePacket) -> None:
            if event.channel == "ff31":
                await adapter.stop()

        async def status(name: str, value: object) -> None:
            states.append((name, value))

        adapter = SerialAdapter(
            SerialConfig(handshake_timeout_ms=30),
            receive_and_stop,
            status,
            ready,
            candidate_provider=lambda _config: ["/dev/ttyUSB0"],
            serial_factory=lambda _path, _config: client,
        )
        await adapter.run()

        self.assertEqual(client.writes, [b"\xE0"])
        self.assertEqual(
            states,
            [
                ("connectionState", "connecting"),
                ("connectionState", "validated"),
                ("connectionState", "disconnected"),
            ],
        )

    async def test_candidate_without_active_ack_01_never_receives_e1(self) -> None:
        client = SlowEmptySerial([])
        adapter: SerialAdapter

        async def stop_after_error(_reason: str) -> None:
            await adapter.stop()

        adapter = SerialAdapter(
            SerialConfig(handshake_timeout_ms=10, command_response_timeout_ms=30),
            noop,
            noop,
            ready,
            error=stop_after_error,
            candidate_provider=lambda _config: ["/dev/ttyUSB1"],
            serial_factory=lambda _path, _config: client,
        )
        await adapter.run()
        self.assertEqual(client.writes, [HANDSHAKE])

    async def test_active_ack_does_not_require_persisted_usb_identity(self) -> None:
        client = AckAndStartDrivenSerial(frame(9, 5, 64))
        adapter: SerialAdapter

        async def receive_and_stop(event: DevicePacket) -> None:
            if event.channel == "ff31":
                await adapter.stop()

        adapter = SerialAdapter(
            SerialConfig(handshake_timeout_ms=10, command_response_timeout_ms=30),
            receive_and_stop,
            noop,
            ready,
            candidate_provider=lambda _config: ["/dev/ttyUSB0"],
            serial_factory=lambda _path, _config: client,
            identity_provider=lambda _path: {
                "resolvedPath": "/dev/ttyUSB0",
                "vid": None,
                "pid": None,
                "usbSerial": None,
                "interface": None,
                "driver": None,
                "usbParent": None,
                "physicalPath": None,
            },
        )
        await adapter.run()
        self.assertEqual(client.writes, [HANDSHAKE, START_COMMAND, b"\xE0"])

    async def test_ack_01_validates_before_e1_produces_a_frame(self) -> None:
        client = AckAndStartDrivenSerial(None)
        states: list[tuple[str, object]] = []
        adapter: SerialAdapter

        async def status(name: str, value: object) -> None:
            states.append((name, value))

        async def stop_after_error(_reason: str) -> None:
            await adapter.stop()

        adapter = SerialAdapter(
            SerialConfig(
                handshake_timeout_ms=10,
                command_response_timeout_ms=30,
                data_timeout_seconds=0.03,
            ),
            noop,
            status,
            ready,
            error=stop_after_error,
            candidate_provider=lambda _config: ["/dev/ttyUSB0"],
            serial_factory=lambda _path, _config: client,
        )
        await adapter.run()
        self.assertEqual(client.writes, [HANDSHAKE, START_COMMAND, b"\xE0"])
        self.assertIn(("connectionState", "validated"), states)
        self.assertNotIn(("connectionState", "validation_failed"), states)

    async def test_algorithm_not_ready_after_handshake_validation_blocks_e1(self) -> None:
        client = AckAndStartDrivenSerial(None)
        states: list[tuple[str, object]] = []
        errors: list[str] = []
        adapter: SerialAdapter

        async def not_ready() -> bool:
            return False

        async def status(name: str, value: object) -> None:
            states.append((name, value))

        async def stop_after_error(reason: str) -> None:
            errors.append(reason)
            await adapter.stop()

        adapter = SerialAdapter(
            SerialConfig(handshake_timeout_ms=10, command_response_timeout_ms=30),
            noop,
            status,
            not_ready,
            error=stop_after_error,
            candidate_provider=lambda _config: ["/dev/ttyUSB-test"],
            serial_factory=lambda _path, _config: client,
        )
        with self.assertLogs("neurobridge.serial.adapter", level="ERROR") as logs:
            await adapter.run()

        self.assertEqual(client.writes, [HANDSHAKE])
        self.assertEqual(
            states,
            [
                ("connectionState", "connecting"),
                ("connectionState", "validating"),
                ("connectionState", "validated"),
                ("connectionState", "disconnected"),
            ],
        )
        self.assertIn("startCommandSent=false", "\n".join(logs.output))
        self.assertIn("Local algorithm is not ready", errors[0])

    async def test_handshake_ack_timeout_reports_validation_failure_and_never_sends_e1(self) -> None:
        client = SlowEmptySerial([])
        states: list[tuple[str, object]] = []
        packets: list[DevicePacket] = []
        adapter: SerialAdapter

        async def packet(event: DevicePacket) -> None:
            packets.append(event)

        async def status(name: str, value: object) -> None:
            states.append((name, value))

        async def stop_after_error(_reason: str) -> None:
            await adapter.stop()

        adapter = SerialAdapter(
            SerialConfig(handshake_timeout_ms=10, command_response_timeout_ms=30),
            packet,
            status,
            ready,
            error=stop_after_error,
            candidate_provider=lambda _config: ["/dev/ttyUSB-test"],
            serial_factory=lambda _path, _config: client,
        )
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            await adapter.run()

        self.assertEqual(client.writes, [HANDSHAKE])
        self.assertEqual(packets, [])
        self.assertEqual(
            states,
            [
                ("connectionState", "connecting"),
                ("connectionState", "validating"),
                ("connectionState", "validation_failed"),
            ],
        )
        self.assertIn("none returned standalone 0x01", "\n".join(logs.output))

    async def test_non_frame_existing_bytes_then_ack_timeout_never_sends_e1(self) -> None:
        client = SlowEmptySerial([HANDSHAKE, b"\xAA"])
        states: list[tuple[str, object]] = []
        packets: list[DevicePacket] = []
        adapter: SerialAdapter

        async def status(name: str, value: object) -> None:
            states.append((name, value))

        async def stop_after_error(_reason: str) -> None:
            await adapter.stop()

        adapter = SerialAdapter(
            SerialConfig(handshake_timeout_ms=10, command_response_timeout_ms=30),
            packets.append,
            status,
            ready,
            error=stop_after_error,
            candidate_provider=lambda _config: ["/dev/ttyUSB-test"],
            serial_factory=lambda _path, _config: client,
        )
        with self.assertLogs("neurobridge.serial.adapter", level="WARNING") as logs:
            await adapter.run()

        self.assertEqual(client.writes, [HANDSHAKE])
        self.assertEqual(packets, [])
        self.assertIn(("connectionState", "validation_failed"), states)
        rendered = "\n".join(logs.output)
        self.assertIn("none returned standalone 0x01", rendered)
        self.assertNotIn("connectionState=validated", rendered)
