from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import errno
import json
import logging
import os
from pathlib import Path
import tempfile
import subprocess
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from neurobridge.ble.flowtime import FF51, FlowtimeAdapter
from neurobridge.ble.packets import DataWindow, RawPacket
from neurobridge.business.gateway import ClientSession, Gateway
from neurobridge.config import AlgorithmConfig, BleConfig, GatewayConfig, RecordingConfig, ServerConfig
from neurobridge.diagnostics import NetworkDiagnostics, network_snapshot


def configuration(root: Path) -> GatewayConfig:
    return GatewayConfig(
        ServerConfig("127.0.0.1", 8765, "/neurobridge/v1/ws", send_timeout_seconds=0.1, delivery_queue_size=2),
        BleConfig(True, "Flowtime Headband", "profile", 1, 0),
        RecordingConfig(root, None, None, 1), AlgorithmConfig(False, ()),
    )


def window(at: int = 1000) -> DataWindow:
    result = DataWindow(at, at + 600)
    result.append(RawPacket("ff31", at + 1, b"e" * 20))
    return result


class DeliveryIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.gateway = Gateway(configuration(self.root))
        await self.gateway.start()
        await self.gateway.update_status("connectionState", "connected")

    async def asyncTearDown(self) -> None:
        await self.gateway.stop()
        self.directory.cleanup()

    async def test_network_failure_preserves_recording_algorithm_and_healthy_client(self) -> None:
        class Algorithm:
            available = True
            error = None
            async def evaluate(self, _: DataWindow) -> tuple[dict, list[str]]:
                return {"attention": 7}, []
            async def stop(self) -> None:
                pass
        self.gateway.algorithm = Algorithm()
        recording_id = self.gateway.store.recording_id
        retired, delivered = asyncio.Event(), asyncio.Event()
        broken = ClientSession(close_transport=AsyncMock(side_effect=retired.set))
        healthy = ClientSession()
        events: list[dict] = []

        async def failed_send(_: dict) -> None:
            raise OSError(errno.ENETUNREACH, "network unavailable")

        async def healthy_send(item: dict) -> None:
            events.append(item)
            delivered.set()

        await self.gateway.subscribe(broken, {"streams": ["eeg.raw", "status"]}, failed_send)
        await self.gateway.subscribe(healthy, {"streams": ["eeg.raw", "status"]}, healthy_send)
        for at, characteristic, value in [(1000, "ff31", b"e" * 20), (1100, "ff51", b"h"), (1700, "ff31", b"f" * 20)]:
            with patch("neurobridge.business.gateway.now_ms", return_value=at):
                await self.gateway.receive_packet(characteristic, value)
        await asyncio.wait_for(retired.wait(), timeout=1)
        await asyncio.wait_for(delivered.wait(), timeout=1)
        self.assertTrue(self.gateway.live)
        self.assertEqual(self.gateway.store.recording_id, recording_id)
        self.assertIsNone(self.gateway.connection_error)
        self.assertNotIn(broken, self.gateway.sessions)
        self.assertIn(healthy, self.gateway.sessions)
        session_dir = self.root / "sessions" / str(recording_id)
        rows = [json.loads(line) for line in (session_dir / "raw/eeg.jsonl").read_text().splitlines()]
        self.assertEqual([base64.b64decode(row["bytesBase64"]) for row in rows], [b"e" * 20, b"f" * 20])
        self.assertTrue((session_dir / "algorithm/attention.jsonl").read_text())
        self.assertTrue(any(item["data"]["event"] == "data" for item in events))
        # Acquisition and a later reconnect remain usable after the failed peer.
        await self.gateway.update_status("connectionState", "disconnected")
        await asyncio.sleep(0)
        await self.gateway.update_status("connectionState", "connected")
        self.assertNotEqual(self.gateway.store.recording_id, recording_id)

    async def test_failed_status_send_does_not_become_a_bluetooth_failure(self) -> None:
        retired = asyncio.Event()
        session = ClientSession(close_transport=AsyncMock(side_effect=retired.set))
        failed_send = AsyncMock(side_effect=ConnectionResetError(errno.ECONNRESET, "reset"))
        await self.gateway.subscribe(session, {"streams": ["status"]}, failed_send)
        await self.gateway.update_status("wearState", "test-state")
        await asyncio.wait_for(retired.wait(), timeout=1)
        self.assertTrue(self.gateway.live)
        self.assertIsNone(self.gateway.connection_error)
        self.assertEqual(failed_send.await_count, 1)

    async def test_stalled_sender_cannot_block_capture_or_another_client(self) -> None:
        entered, cancelled, retired = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.gateway.config = replace(self.gateway.config, server=replace(self.gateway.config.server, send_timeout_seconds=.1))
        slow = ClientSession(close_transport=AsyncMock(side_effect=retired.set))
        healthy = ClientSession()
        received: asyncio.Queue[dict] = asyncio.Queue()

        async def blocked_send(_: dict) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def healthy_send(item: dict) -> None:
            received.put_nowait(item)

        await self.gateway.subscribe(slow, {"streams": ["eeg.raw"]}, blocked_send)
        await self.gateway.subscribe(healthy, {"streams": ["eeg.raw"]}, healthy_send)
        await asyncio.wait_for(self.gateway.publish_window(window()), timeout=0.5)
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.wait_for(received.get(), timeout=1)
        for at in (1600, 2200, 2800):
            await asyncio.wait_for(self.gateway.publish_window(window(at)), timeout=0.5)
            await asyncio.wait_for(received.get(), timeout=1)
        await asyncio.wait_for(retired.wait(), timeout=1)
        self.assertTrue(cancelled.is_set())
        self.assertNotIn(slow, self.gateway.sessions)
        self.assertIn(healthy, self.gateway.sessions)
        self.assertTrue(self.gateway.live)

    async def test_send_timeout_closes_only_the_stalled_client(self) -> None:
        retired = asyncio.Event()
        session = ClientSession(close_transport=AsyncMock(side_effect=retired.set))
        async def blocked_send(_: dict) -> None:
            await asyncio.Event().wait()
        await self.gateway.subscribe(session, {"streams": ["status"]}, blocked_send)
        await self.gateway.broadcast_status()
        await asyncio.wait_for(retired.wait(), timeout=1)
        self.assertTrue(self.gateway.live)
        self.assertFalse(session.subscriptions)

    async def test_response_failure_is_not_retried_as_internal_error(self) -> None:
        retired = asyncio.Event()
        session = ClientSession(close_transport=AsyncMock(side_effect=retired.set))
        failed_send = AsyncMock(side_effect=BrokenPipeError(errno.EPIPE, "closed"))
        await self.gateway.handle(session, json.dumps({"protocolVersion": "1.0", "messageType": "request", "requestId": "request", "action": "getStatus", "params": {}}), failed_send)
        await asyncio.wait_for(retired.wait(), timeout=1)
        self.assertEqual(failed_send.await_count, 1)
        self.assertTrue(self.gateway.live)


class BluetoothRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_error_is_observed_and_later_packets_are_processed(self) -> None:
        calls: list[bytes] = []
        async def packet(packet) -> None:
            value = packet.value
            calls.append(value)
            if len(calls) == 1:
                raise OSError(errno.EIO, "capture failure")
        adapter = FlowtimeAdapter(configuration(Path("/tmp")).ble, packet, AsyncMock(), AsyncMock())
        handlers = {}
        async def start_notify(characteristic, callback):
            handlers[characteristic] = callback
        adapter._client = SimpleNamespace(start_notify=start_notify, services=SimpleNamespace(get_characteristic=lambda _: None))
        await adapter._subscribe()
        value = bytearray(b"a")
        with self.assertLogs("neurobridge.ble.flowtime", level="ERROR") as logs:
            handlers[FF51](0, value)
            value[0] = ord("z")
            await adapter._drain_callbacks()
        handlers[FF51](0, bytearray(b"b"))
        await adapter._drain_callbacks()
        self.assertEqual(calls, [b"a", b"b"])
        self.assertIn("callback processing failed", "\n".join(logs.output))
        adapter.status.assert_not_awaited()

    async def test_disconnect_reconnect_and_stale_callback_are_serialized(self) -> None:
        clients = []
        first_ready, second_ready = asyncio.Event(), asyncio.Event()
        async def status(name, value):
            if name == "connectionState" and value == "connected":
                (first_ready if len(clients) == 1 else second_ready).set()
        class Client:
            def __init__(self, _, disconnected_callback):
                self.disconnected_callback = disconnected_callback
                self.is_connected = False
                self.services = SimpleNamespace(get_characteristic=lambda _: None)
                clients.append(self)
            async def connect(self): self.is_connected = True
            async def start_notify(self, *_): pass
            async def write_gatt_char(self, *_args, **_kwargs): pass
            async def disconnect(self):
                self.is_connected = False
                self.disconnected_callback(self)
        device = SimpleNamespace(name="Flowtime Headband", metadata={"uuids": ["profile"]}, rssi=-65)
        adapter = FlowtimeAdapter(configuration(Path("/tmp")).ble, AsyncMock(), status, AsyncMock())
        fake_bleak = SimpleNamespace(BleakClient=Client, BleakScanner=SimpleNamespace(discover=AsyncMock(return_value=[device])))
        with patch.dict("sys.modules", {"bleak": fake_bleak}):
            task = asyncio.create_task(adapter.run())
            try:
                await asyncio.wait_for(first_ready.wait(), timeout=1)
                await clients[0].disconnect()
                await asyncio.wait_for(second_ready.wait(), timeout=1)
                clients[0].disconnected_callback(clients[0])
                self.assertFalse(adapter._disconnected.is_set())
                self.assertTrue(clients[1].is_connected)
                await adapter.stop()
                await asyncio.wait_for(task, timeout=1)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


class NetworkDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def test_snapshot_includes_carrier_addresses_routes_and_neighbour_state_without_mac(self) -> None:
        snapshot = network_snapshot(
            [{"ifname": "eth0", "operstate": "UP", "flags": ["UP", "LOWER_UP"], "address": "PRIVATE_MAC", "addr_info": [{"family": "inet", "local": "192.168.88.10", "prefixlen": 24}]}],
            [{"dst": "192.168.88.0/24", "dev": "eth0", "protocol": "kernel"}],
            [{"dst": "192.168.88.20", "dev": "eth0", "state": ["FAILED"], "lladdr": "PRIVATE_MAC"}],
            "192.168.88.10", "eth0",
        )
        self.assertTrue(snapshot["hostAssigned"])
        self.assertTrue(snapshot["links"][0]["carrier"])
        self.assertEqual(snapshot["neighbours"][0]["state"], ["FAILED"])
        self.assertNotIn("PRIVATE_MAC", json.dumps(snapshot))

    async def test_snapshot_failure_does_not_terminate_the_monitor(self) -> None:
        config = configuration(Path("/tmp"))
        config = replace(config, logging=replace(config.logging, diagnostics_interval_seconds=0.01))
        monitor = NetworkDiagnostics(config, "boot-test")
        recovered = asyncio.Event()
        calls = 0
        async def ip_json(*_):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise FileNotFoundError("ip")
            recovered.set()
            return []
        with patch("neurobridge.diagnostics.sys.platform", "linux"), patch("neurobridge.diagnostics._ip_json", side_effect=ip_json):
            task = asyncio.create_task(monitor.run())
            try:
                await asyncio.wait_for(recovered.wait(), timeout=1)
                self.assertFalse(task.done())
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


class RuntimeDiagnosticCollectorTests(unittest.TestCase):
    def test_collector_exports_rotations_and_failure_status_without_config_or_symlink_contents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands, logs, output = root / "bin", root / "logs", root / "output with spaces"
            commands.mkdir()
            logs.mkdir()
            (commands / "timeout").write_text('#!/bin/sh\nshift\nexec "$@"\n')
            for name in ("journalctl", "systemctl", "ip", "ss", "bluetoothctl", "rfkill"):
                (commands / name).write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 7\n')
            for command in commands.iterdir():
                command.chmod(0o755)
            (logs / "neurobridge.log").write_text("current log")
            (logs / "neurobridge.log.1.gz").write_bytes(b"rotation")
            (logs / "gateway.toml").write_text("SECRET_CONFIGURATION")
            (logs / "raw.jsonl").write_text("PRIVATE_RAW_BYTES")
            (logs / "secret.log").symlink_to(logs / "gateway.toml")
            script = Path(__file__).resolve().parents[1] / "linux/ubuntu/collect-runtime-diagnostics.sh"
            environment = dict(os.environ, PATH=f"{commands}{os.pathsep}{os.environ['PATH']}")
            environment.pop("SUDO_USER", None)
            result = subprocess.run(["bash", str(script), "--since", "2026-10-30 10:30:00", "--log-dir", str(logs), "--output-dir", str(output)], env=environment, capture_output=True, text=True, timeout=10, check=True)
            archives = list(output.glob("*.tar.gz"))
            self.assertEqual(len(archives), 1)
            self.assertIn(str(archives[0]), result.stdout)
            self.assertEqual(archives[0].stat().st_mode & 0o777, 0o600)
            with tarfile.open(archives[0]) as bundle:
                names = bundle.getnames()
                self.assertIn("./runtime-logs/neurobridge.log", names)
                self.assertIn("./runtime-logs/neurobridge.log.1.gz", names)
                self.assertNotIn("./runtime-logs/secret.log", names)
                content = b"\n".join(bundle.extractfile(item).read() for item in bundle.getmembers() if item.isfile())
                self.assertIn(b"2026-10-30 10:30:00", content)
                self.assertIn(b"exitCode=7", content)
                self.assertNotIn(b"SECRET_CONFIGURATION", content)
                self.assertNotIn(b"PRIVATE_RAW_BYTES", content)


class DurableLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_failure_is_persisted_and_library_payload_debug_is_filtered(self) -> None:
        from neurobridge.__main__ import run
        root_logger = logging.getLogger()
        handlers, level = root_logger.handlers[:], root_logger.level
        library_loggers = [logging.getLogger(name) for name in ("websockets", "bleak", "websockets.server")]
        levels = [logger.level for logger in library_loggers]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            config = path / "gateway.toml"
            config.write_text('[data_source]\ntype="bluetooth"\n' + f'[logging]\ndirectory = {json.dumps(directory)}\nlevel = "DEBUG"\n[recording]\ndirectory = {json.dumps(str(path / "recordings"))}\n[algorithm]\nenabled = false\n')
            try:
                from neurobridge.bootstrap import build_container
                from neurobridge.config import load
                from neurobridge.profiles.resolver import RuntimePlatform
                runtime_config = load(config)
                from neurobridge.config import AccessConfig, LocalUiConfig
                runtime_config = replace(runtime_config, server=replace(runtime_config.server, host="192.168.88.10"), access=AccessConfig("wired_b_side"), local_ui=LocalUiConfig(enabled=False))
                container = build_container(runtime_config, RuntimePlatform("ubuntu", "x86_64"))
                with patch("neurobridge.__main__.build_container", return_value=container), patch("neurobridge.__main__.load_runtime_config", return_value=runtime_config), patch("neurobridge.__main__.serve", AsyncMock(side_effect=OSError(errno.EADDRNOTAVAIL, "listener address unavailable"))):
                    with self.assertRaises(ExceptionGroup):
                        await run(str(config))
                logging.getLogger("websockets.server").setLevel(logging.DEBUG)
                logging.getLogger("websockets.server").debug("PRIVATE_FULL_FRAME")
                contents = (path / "neurobridge.log").read_text()
                self.assertIn("Gateway runtime task failed", contents)
                self.assertIn("listener address unavailable", contents)
                self.assertNotIn("PRIVATE_FULL_FRAME", contents)
            finally:
                for handler in root_logger.handlers:
                    if handler not in handlers:
                        handler.close()
                root_logger.handlers = handlers
                root_logger.setLevel(level)
                for logger, previous in zip(library_loggers, levels):
                    logger.setLevel(previous)


if __name__ == "__main__":
    unittest.main()
