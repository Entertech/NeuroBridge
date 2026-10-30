from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest

import websockets


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("b_client_probe", ROOT / "tools/b-client-probe.py")
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class BClientProbeTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, *, mode="live", available=True, valid=True, emit=True, error=False):
        actions = []

        async def handler(ws, _path):
            self.assertEqual(ws.subprotocol, "neurobridge.v1")
            async for raw in ws:
                req = json.loads(raw)
                action = req["action"]
                actions.append(action)
                if error:
                    await ws.send(json.dumps({"protocolVersion": "1.0", "code": 409, "data": {}, "message": "Unavailable"}))
                    continue
                if action == "getStatus":
                    result = {
                        "connectionState": "connected" if mode == "live" else "disconnected",
                        "algorithmState": "unavailable", "mode": mode,
                        "availableStreams": ["eeg.raw"] if available else ["status"],
                    }
                else:
                    result = {"subscriptionId": "test-sub"}
                await ws.send(json.dumps({
                    "protocolVersion": "1.0", "code": 200, "message": "OK",
                    "data": {"requestId": req["requestId"], "result": result},
                }))
                if action == "subscribe" and emit:
                    await ws.send(json.dumps({
                        "protocolVersion": "1.0", "code": 200, "message": "OK",
                        "data": {
                            "event": "data", "subscriptionId": "test-sub", "mode": mode,
                            "valid": valid, "timestampMs": 123456,
                            "payload": {"eegRaw": {
                                "byteLength": 20, "packetCount": 1,
                                "bytesBase64": "PRIVATE_BYTES_NEVER_PRINT",
                            }},
                        },
                    }))

        async with websockets.serve(
            handler, "127.0.0.1", 0, subprotocols=["neurobridge.v1"], ping_interval=None,
        ) as server:
            port = server.sockets[0].getsockname()[1]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                result = await PROBE.probe(f"ws://127.0.0.1:{port}/neurobridge/v1/ws", seconds=0.1, max_batches=1)
        self.assertNotIn("PRIVATE_BYTES_NEVER_PRINT", out.getvalue())
        return result, out.getvalue(), actions

    async def test_live_data_is_verified_without_printing_raw_payload(self):
        result, output, actions = await self.exercise()
        self.assertEqual(result, 0)
        self.assertIn("PASS: received valid live data", output)
        self.assertEqual(actions, ["getStatus", "subscribe", "unsubscribe"])

    async def test_replay_does_not_claim_live_capture_was_verified(self):
        result, output, _ = await self.exercise(mode="replay")
        self.assertEqual(result, 0)
        self.assertIn("REPLAY ONLY", output)
        self.assertNotIn("PASS:", output)

    async def test_invalid_data_does_not_pass(self):
        result, output, _ = await self.exercise(valid=False)
        self.assertEqual(result, 2)
        self.assertIn("NO VALID DATA", output)

    async def test_status_only_does_not_create_a_subscription(self):
        result, output, actions = await self.exercise(available=False)
        self.assertEqual(result, 2)
        self.assertIn("NO DATA", output)
        self.assertEqual(actions, ["getStatus"])

    async def test_successful_subscription_without_events_does_not_pass(self):
        result, output, actions = await self.exercise(emit=False)
        self.assertEqual(result, 2)
        self.assertIn("NO VALID DATA", output)
        self.assertEqual(actions, ["getStatus", "subscribe", "unsubscribe"])

    async def test_gateway_error_is_propagated(self):
        with self.assertRaisesRegex(PROBE.ProbeError, "Gateway error code=409"):
            await self.exercise(error=True)


if __name__ == "__main__":
    unittest.main()
