from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
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


class StartDrivenSerial(SlowEmptySerial):
    def __init__(self, resumed_frame: bytes | None) -> None:
        super().__init__([])
        self.resumed_frame = resumed_frame

    def write(self, value: bytes) -> int:
        written = super().write(value)
        if value == START_COMMAND and self.resumed_frame is not None:
            self.reads.append(self.resumed_frame)
            self.resumed_frame = None
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
    def stable_identity(usb_serial: str = "HEADBAND-001") -> dict[str, str | None]:
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

    async def test_fixed_handshake_stops_candidate_probe_before_ack_exchange(self) -> None:
        client = FakeSerial([b"ignored" + HANDSHAKE])
        adapter = SerialAdapter(SerialConfig(), noop, noop, noop)
        accepted = await adapter._await_handshake(client, "/dev/ttyACM0", 1, 1, 3)
        self.assertTrue(accepted)
        self.assertEqual(client.writes, [])

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
        self.assertIn("phase=handshake", rendered)
        self.assertNotIn("No serial candidate produced the fixed handshake", rendered)

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
        client = FakeSerial([HANDSHAKE, HANDSHAKE, b"\x01", frame(7, 4, 63)])
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
            SerialConfig(command_response_timeout_ms=30),
            packet,
            status,
            ready,
            candidate_provider=lambda _config: ["/dev/ttyACM-test"],
            serial_factory=lambda _path, _config: client,
        )
        await adapter.run()
        self.assertEqual(client.writes, [HANDSHAKE, HANDSHAKE, b"\xE1", b"\xE0"])
        self.assertEqual([channel for channel, _value in packets], ["serial.frame", "ff31", "ff51"])
        self.assertEqual(
            states,
            [
                ("connectionState", "connecting"),
                ("connectionState", "connected"),
                ("connectionState", "validating"),
                ("connectionState", "validated"),
                ("connectionState", "disconnected"),
            ],
        )
        self.assertEqual(states[-1], ("connectionState", "disconnected"))
        self.assertTrue(client.closed)

    async def test_process_restart_resumes_trusted_device_without_second_handshake(self) -> None:
        with TemporaryDirectory() as directory:
            state_file = Path(directory) / "serial-device-identity.json"
            config = SerialConfig(
                handshake_timeout_ms=10,
                command_response_timeout_ms=30,
                data_timeout_seconds=0.1,
                identity_state_file=state_file,
            )
            identity = self.stable_identity()

            first_client = FakeSerial([HANDSHAKE, b"\x01", frame(7, 4, 63)])
            first_adapter: SerialAdapter

            async def stop_first(event: DevicePacket) -> None:
                if event.channel == "ff31":
                    await first_adapter.stop()

            first_adapter = SerialAdapter(
                config,
                stop_first,
                noop,
                ready,
                candidate_provider=lambda _config: ["/dev/ttyUSB0"],
                serial_factory=lambda _path, _config: first_client,
                identity_provider=lambda _path: identity,
            )
            await first_adapter.run()
            self.assertTrue(state_file.is_file())
            self.assertEqual(first_client.writes, [HANDSHAKE, START_COMMAND, b"\xE0"])

            resumed_client = StartDrivenSerial(frame(8, 5, 64))
            resumed_adapter: SerialAdapter

            async def stop_resumed(event: DevicePacket) -> None:
                if event.channel == "ff31":
                    await resumed_adapter.stop()

            resumed_adapter = SerialAdapter(
                config,
                stop_resumed,
                noop,
                ready,
                candidate_provider=lambda _config: ["/dev/ttyUSB0"],
                serial_factory=lambda _path, _config: resumed_client,
                identity_provider=lambda _path: identity,
            )
            await resumed_adapter.run()
            self.assertEqual(resumed_client.writes, [START_COMMAND, b"\xE0"])

    async def test_new_power_on_handshake_takes_precedence_over_saved_identity(self) -> None:
        with TemporaryDirectory() as directory:
            state_file = Path(directory) / "serial-device-identity.json"
            config = SerialConfig(
                handshake_timeout_ms=10,
                command_response_timeout_ms=30,
                identity_state_file=state_file,
            )
            identity = self.stable_identity()
            seed = SerialAdapter(config, noop, noop, noop)
            seed._save_trusted_identity("/dev/ttyUSB0", identity)
            client = FakeSerial([HANDSHAKE, b"\x01", frame(9, 5, 64)])
            adapter: SerialAdapter

            async def receive_and_stop(event: DevicePacket) -> None:
                if event.channel == "ff31":
                    await adapter.stop()

            adapter = SerialAdapter(
                config,
                receive_and_stop,
                noop,
                ready,
                candidate_provider=lambda _config: ["/dev/ttyUSB0"],
                serial_factory=lambda _path, _config: client,
                identity_provider=lambda _path: identity,
            )
            await adapter.run()
            self.assertEqual(client.writes, [HANDSHAKE, START_COMMAND, b"\xE0"])

    async def test_trusted_identity_mismatch_never_sends_e1(self) -> None:
        with TemporaryDirectory() as directory:
            state_file = Path(directory) / "serial-device-identity.json"
            config = SerialConfig(
                handshake_timeout_ms=10,
                identity_state_file=state_file,
            )
            seed = SerialAdapter(config, noop, noop, noop)
            seed._save_trusted_identity("/dev/ttyUSB0", self.stable_identity())
            unknown_client = SlowEmptySerial([])
            adapter: SerialAdapter

            async def stop_after_error(_reason: str) -> None:
                await adapter.stop()

            adapter = SerialAdapter(
                config,
                noop,
                noop,
                ready,
                error=stop_after_error,
                candidate_provider=lambda _config: ["/dev/ttyUSB1"],
                serial_factory=lambda _path, _config: unknown_client,
                identity_provider=lambda _path: self.stable_identity("OTHER-DEVICE"),
            )
            await adapter.run()
            self.assertEqual(unknown_client.writes, [])

    async def test_first_handshake_blocks_e1_when_identity_cannot_be_persisted_safely(self) -> None:
        with TemporaryDirectory() as directory:
            state_file = Path(directory) / "serial-device-identity.json"
            config = SerialConfig(
                command_response_timeout_ms=30,
                identity_state_file=state_file,
            )
            client = FakeSerial([HANDSHAKE, b"\x01"])
            states: list[tuple[str, object]] = []
            adapter: SerialAdapter

            async def status(name: str, value: object) -> None:
                states.append((name, value))

            async def stop_after_error(_reason: str) -> None:
                await adapter.stop()

            adapter = SerialAdapter(
                config,
                noop,
                status,
                ready,
                error=stop_after_error,
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
            self.assertEqual(client.writes, [HANDSHAKE])
            self.assertIn(("connectionState", "validation_failed"), states)
            self.assertFalse(state_file.exists())

    async def test_trusted_resume_requires_valid_frame_and_stops_after_timeout(self) -> None:
        with TemporaryDirectory() as directory:
            state_file = Path(directory) / "serial-device-identity.json"
            config = SerialConfig(
                handshake_timeout_ms=10,
                data_timeout_seconds=0.03,
                identity_state_file=state_file,
            )
            identity = self.stable_identity()
            seed = SerialAdapter(config, noop, noop, noop)
            seed._save_trusted_identity("/dev/ttyUSB0", identity)
            silent_client = StartDrivenSerial(None)
            states: list[tuple[str, object]] = []
            adapter: SerialAdapter

            async def status(name: str, value: object) -> None:
                states.append((name, value))

            async def stop_after_error(_reason: str) -> None:
                await adapter.stop()

            adapter = SerialAdapter(
                config,
                noop,
                status,
                ready,
                error=stop_after_error,
                candidate_provider=lambda _config: ["/dev/ttyUSB0"],
                serial_factory=lambda _path, _config: silent_client,
                identity_provider=lambda _path: identity,
            )
            await adapter.run()
            self.assertEqual(silent_client.writes, [START_COMMAND, b"\xE0"])
            self.assertIn(("connectionState", "validation_failed"), states)
            self.assertNotIn(("connectionState", "validated"), states)

    async def test_restart_after_crash_accepts_preexisting_valid_stream_from_trusted_device(self) -> None:
        with TemporaryDirectory() as directory:
            state_file = Path(directory) / "serial-device-identity.json"
            config = SerialConfig(
                handshake_timeout_ms=10,
                data_timeout_seconds=0.1,
                identity_state_file=state_file,
            )
            identity = self.stable_identity()
            seed = SerialAdapter(config, noop, noop, noop)
            seed._save_trusted_identity("/dev/ttyUSB0", identity)
            streaming_client = FakeSerial([frame(21, 6, 65), frame(22, 7, 66)])
            packets: list[DevicePacket] = []
            adapter: SerialAdapter

            async def receive_and_stop(event: DevicePacket) -> None:
                packets.append(event)
                if event.channel == "ff31":
                    await adapter.stop()

            adapter = SerialAdapter(
                config,
                receive_and_stop,
                noop,
                ready,
                candidate_provider=lambda _config: ["/dev/ttyUSB0"],
                serial_factory=lambda _path, _config: streaming_client,
                identity_provider=lambda _path: identity,
            )
            await adapter.run()
            self.assertEqual(streaming_client.writes, [b"\xE0"])
            self.assertEqual(
                [event.channel for event in packets],
                ["serial.frame", "ff31", "ff51"],
            )

    async def test_algorithm_not_ready_after_handshake_validation_blocks_e1(self) -> None:
        client = FakeSerial([HANDSHAKE, b"\x01"])
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
            SerialConfig(command_response_timeout_ms=30),
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
                ("connectionState", "connected"),
                ("connectionState", "validating"),
                ("connectionState", "validation_failed"),
                ("connectionState", "disconnected"),
            ],
        )
        self.assertIn("startCommandSent=false", "\n".join(logs.output))
        self.assertIn("Local algorithm is not ready", errors[0])

    async def test_handshake_ack_timeout_reports_validation_failure_and_never_sends_e1(self) -> None:
        client = FakeSerial([HANDSHAKE])
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
            SerialConfig(command_response_timeout_ms=30),
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
                ("connectionState", "connected"),
                ("connectionState", "validating"),
                ("connectionState", "validation_failed"),
                ("connectionState", "disconnected"),
            ],
        )
        self.assertIn("reason=response_timeout", "\n".join(logs.output))

    async def test_partial_repeated_handshake_times_out_and_never_sends_e1(self) -> None:
        client = FakeSerial([HANDSHAKE, b"\xAA"])
        states: list[tuple[str, object]] = []
        packets: list[DevicePacket] = []
        adapter: SerialAdapter

        async def status(name: str, value: object) -> None:
            states.append((name, value))

        async def stop_after_error(_reason: str) -> None:
            await adapter.stop()

        adapter = SerialAdapter(
            SerialConfig(command_response_timeout_ms=30),
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
        self.assertIn("partialHandshakeBytes=1", rendered)
        self.assertIn("reason=response_timeout", rendered)
        self.assertNotIn("validation succeeded", rendered)
