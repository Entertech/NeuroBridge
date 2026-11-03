from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import AsyncMock

import websockets

from neurobridge.config import AlgorithmConfig, BleConfig, GatewayConfig, RecordingConfig, ServerConfig
from neurobridge.business.gateway import Gateway
from neurobridge.ble.packets import DataWindow, RawPacket
from neurobridge.northbound.websocket import SUBPROTOCOL, create_server


def unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class WebSocketIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.port = unused_port()
        config = GatewayConfig(
            ServerConfig("127.0.0.1", self.port, "/neurobridge/v1/ws"),
            BleConfig(False, "Flowtime Headband", "0000ff10-1212-abcd-1523-785feabcd123", 5, 3),
            RecordingConfig(Path(self.directory.name), "SUBJECT-001", None, 1),
            AlgorithmConfig(False, ()),
        )
        self.gateway = Gateway(config)
        await self.gateway.start()
        self.server = await create_server(self.gateway)
        self.url = f"ws://127.0.0.1:{self.port}/neurobridge/v1/ws"

    async def asyncTearDown(self) -> None:
        self.server.close()
        await self.server.wait_closed()
        await self.gateway.stop()
        self.directory.cleanup()

    async def test_requires_neurobridge_subprotocol(self) -> None:
        for offered in (None, ["other.v1"]):
            with self.subTest(offered=offered):
                with self.assertRaises(websockets.exceptions.InvalidStatusCode) as error:
                    await websockets.connect(self.url, subprotocols=offered)
                self.assertEqual(error.exception.status_code, 426)

    async def test_status_request_uses_negotiated_subprotocol_and_contract_envelope(self) -> None:
        async with websockets.connect(self.url, subprotocols=[SUBPROTOCOL], ping_interval=None) as client:
            self.assertEqual(client.subprotocol, SUBPROTOCOL)
            await client.send(json.dumps({"protocolVersion": "1.0", "messageType": "request", "requestId": "status-1", "action": "getStatus", "params": {}}))
            response = json.loads(await client.recv())
            self.assertEqual(set(response), {"protocolVersion", "code", "data", "message"})
            self.assertEqual(response["code"], 200)
            self.assertEqual(response["data"]["result"]["gatewayBootId"], self.gateway.boot_id)

    async def test_binary_frame_is_rejected(self) -> None:
        async with websockets.connect(self.url, subprotocols=[SUBPROTOCOL], ping_interval=None) as client:
            await client.send(b"not-json")
            with self.assertRaises(websockets.exceptions.ConnectionClosedError) as error:
                await client.recv()
            self.assertEqual(error.exception.code, 1003)

    async def test_corrupt_history_keeps_status_connected_and_healthy_replay_delivers(self) -> None:
        healthy = self.gateway.store.start(1000)
        self.gateway.store.save_algorithm(timestamp_ms=1200, valid=True, invalid_reasons=[], algorithm={"attention": 7})
        self.gateway.store.stop()
        broken = self.gateway.store.start(2000)
        self.gateway.store.stop()
        path = Path(self.directory.name) / "sessions" / broken / "raw/eeg.jsonl"
        path.write_text("\n", encoding="utf-8")
        async with websockets.connect(self.url, subprotocols=[SUBPROTOCOL], ping_interval=None) as client:
            async def request(action, params):
                await client.send(json.dumps({"protocolVersion": "1.0", "messageType": "request", "requestId": action, "action": action, "params": params}))
                return json.loads(await asyncio.wait_for(client.recv(), timeout=1))
            response = await request("getStatus", {})
            self.assertEqual(response["code"], 200)
            self.assertEqual(response["data"]["result"]["connectionState"], "disconnected")
            self.assertIn("eeg", response["data"]["result"]["availableStreams"])
            response = await request("subscribe", {"streams": ["eeg"]})
            self.assertEqual(response["code"], 200)
            event = json.loads(await asyncio.wait_for(client.recv(), timeout=1))
            self.assertEqual(event["data"]["payload"]["algorithm"]["attention"], 7)
            self.assertEqual(self.gateway.replay_recording_id, healthy)

    async def test_failed_sender_is_closed_and_new_connection_can_resubscribe(self) -> None:
        await self.gateway.update_status("connectionState", "connected")
        async def request(client, action, params):
            await client.send(json.dumps({"protocolVersion": "1.0", "messageType": "request", "requestId": action, "action": action, "params": params}))
            return json.loads(await asyncio.wait_for(client.recv(), timeout=1))
        async with websockets.connect(self.url, subprotocols=[SUBPROTOCOL], ping_interval=None) as failed, websockets.connect(self.url, subprotocols=[SUBPROTOCOL], ping_interval=None) as healthy:
            self.assertTrue(all(protocol.ping_interval is None for protocol in self.server.websockets))
            confirmation = await request(failed, "subscribe", {"streams": ["eeg.raw"]})
            old_id = confirmation["data"]["result"]["subscriptionId"]
            await request(healthy, "subscribe", {"streams": ["eeg.raw"]})
            session = next(item for item in self.gateway.sessions if old_id in item.subscriptions)
            session.subscriptions[old_id].send = AsyncMock(side_effect=OSError(101, "network unreachable"))
            window = DataWindow(0, 600)
            window.append(RawPacket("ff31", 1, b"x" * 20))
            await self.gateway.publish_window(window)
            event = json.loads(await asyncio.wait_for(healthy.recv(), timeout=1))
            self.assertEqual(event["data"]["event"], "data")
            await asyncio.wait_for(failed.wait_closed(), timeout=1)
            self.assertEqual(failed.close_code, 1011)
            self.assertTrue(self.gateway.live)
            async with websockets.connect(self.url, subprotocols=[SUBPROTOCOL], ping_interval=None) as restored:
                status = await request(restored, "getStatus", {})
                self.assertEqual(status["data"]["result"]["connectionState"], "connected")
                confirmation = await request(restored, "subscribe", {"streams": ["eeg.raw"]})
                self.assertNotEqual(confirmation["data"]["result"]["subscriptionId"], old_id)
                await self.gateway.publish_window(window)
                self.assertEqual(json.loads(await asyncio.wait_for(restored.recv(), timeout=1))["data"]["event"], "data")
