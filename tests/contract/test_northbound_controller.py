from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from neurobridge.adapters.northbound.controller import NorthboundController
from neurobridge.business.gateway import ClientSession, Gateway, STREAM_NOT_AVAILABLE_REASON
from neurobridge.config import load


class NorthboundControllerContractTests(unittest.IsolatedAsyncioTestCase):
    def gateway(self, directory: str) -> Gateway:
        path = Path(directory) / "gateway.toml"
        path.write_text(
            'profile="kylin_headset_local"\n[data_source]\ntype="serial"\n'
            f'[recording]\ndirectory="{directory}"\n',
            encoding="utf-8",
        )
        return Gateway(load(path))

    async def test_controller_parses_and_dispatches_without_websocket_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = NorthboundController(self.gateway(directory))
            sent = []
            async def send(value):
                sent.append(value)
            request = {
                "protocolVersion": "1.0",
                "messageType": "request",
                "requestId": "status-1",
                "action": "getStatus",
                "params": {},
            }
            await controller.handle(ClientSession(), json.dumps(request), send)
            self.assertEqual(sent[0]["code"], 200)
            self.assertEqual(sent[0]["data"]["action"], "getStatus")

    async def test_serial_offline_data_request_stays_live_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = NorthboundController(self.gateway(directory))
            sent = []
            async def send(value):
                sent.append(value)
            request = {
                "protocolVersion": "1.0",
                "messageType": "request",
                "requestId": "latest-1",
                "action": "getLatest",
                "params": {"streams": ["eeg"]},
            }
            await controller.handle(ClientSession(), json.dumps(request), send)
            self.assertEqual(sent[0]["code"], 409)
            self.assertEqual(sent[0]["data"]["reason"], STREAM_NOT_AVAILABLE_REASON)
            self.assertNotEqual(sent[0]["data"].get("mode"), "replay")
