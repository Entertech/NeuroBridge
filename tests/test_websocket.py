from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket
import tempfile
import unittest

import websockets

from neurobridge.config import AlgorithmConfig, BleConfig, GatewayConfig, RecordingConfig, ServerConfig
from neurobridge.business.gateway import Gateway
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
            BleConfig(False, "0000ff10-1212-abcd-1523-785feabcd123", 5, 3),
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
