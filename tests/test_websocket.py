from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import AsyncMock, PropertyMock, patch

import websockets

from neurobridge.config import AlgorithmConfig, BleConfig, GatewayConfig, RecordingConfig, ServerConfig
from neurobridge.business.gateway import Gateway
from neurobridge.ble.packets import DataWindow, RawPacket
from neurobridge.northbound.websocket import SUBPROTOCOL, create_server, require_subprotocol
from neurobridge.domain.algorithm import AlgorithmResult
from neurobridge.domain.result import WindowResult
from neurobridge.domain.signal import ParsedSignal, ParsedSignalBatch
from websockets.datastructures import Headers


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
        self.origin = "http://127.0.0.1:8080"

    async def asyncTearDown(self) -> None:
        self.server.close()
        await self.server.wait_closed()
        await self.gateway.stop()
        self.directory.cleanup()

    async def test_hrv_trace_reaches_actual_json_send_and_latest_without_wire_changes(self):
        await self.gateway.update_status("connectionState", "connected")
        signal = ParsedSignal("hr", b"P", "bytes", None, {}, ("frame-test",), 500)
        batch = ParsedSignalBatch("batch-hrv", "headband_ble", "conn-test", self.gateway.store.recording_id,
                                  0, 600, (signal,), ("frame-test",))
        result = WindowResult(batch, AlgorithmResult("batch-hrv", None, 600, 600,
                              {"hr": {"value": 80, "hrv": 6.5}}), "live", 600, True)
        with patch.object(type(self.gateway.algorithm), "available", new_callable=PropertyMock, return_value=True):
            with self.assertLogs("neurobridge.hrv", "DEBUG") as logs:
                async with websockets.connect(self.url, subprotocols=[SUBPROTOCOL], ping_interval=None, origin=self.origin) as client:
                    async def request(action):
                        await client.send(json.dumps({"protocolVersion":"1.0", "messageType":"request", "requestId":action,
                                                      "action":action, "params":{"streams":["hr"]}}))
                        return json.loads(await asyncio.wait_for(client.recv(), 1))
                    self.assertEqual((await request("subscribe"))["code"], 200)
                    await self.gateway.publish_window_result(result)
                    event = json.loads(await asyncio.wait_for(client.recv(), 1))
                    self.assertEqual(event["data"]["payload"]["algorithm"]["hr"]["hrv"], 6.5)
                    latest = await request("getLatest")
                    self.assertEqual(latest["data"]["result"]["payload"]["algorithm"]["hr"]["hrv"], 6.5)
                    self.assertEqual(set(latest), {"protocolVersion", "code", "data", "message"})
                    self.assertNotIn("hrvDiagnostics", json.dumps(latest))
                    await asyncio.sleep(0)
        entries = [json.loads(line.split("HRV trace: ", 1)[1]) for line in logs.output if "HRV trace: " in line]
        stages = {entry["stage"] for entry in entries}
        self.assertTrue({"gateway_received", "payload_projection", "delivery_projection", "subscription_sent",
                         "latest_selected", "latest_projection", "latest_response", "websocket_sent"} <= stages, stages)
        self.assertTrue(all(entry["hrvState"] == "number" for entry in entries), entries)
        self.assertTrue(all(entry["timestampMs"] == 600 for entry in entries), entries)
        self.assertNotIn("6.5", " ".join(logs.output))

    async def test_requires_neurobridge_subprotocol(self) -> None:
        for offered in (None, ["other.v1"]):
            with self.subTest(offered=offered):
                with self.assertRaises(websockets.exceptions.InvalidStatusCode) as error:
                    await websockets.connect(self.url, subprotocols=offered, origin=self.origin)
                self.assertEqual(error.exception.status_code, 426)
                self.assertEqual(error.exception.headers['Content-Type'], 'text/plain; charset=utf-8')
        _, _, body = await require_subprotocol('/', Headers())
        self.assertEqual(body.decode('utf-8').strip(), '连接协议不匹配，请使用 neurobridge.v1 子协议')

    async def test_status_request_uses_negotiated_subprotocol_and_contract_envelope(self) -> None:
        async with websockets.connect(self.url, subprotocols=[SUBPROTOCOL], ping_interval=None, origin=self.origin) as client:
            self.assertEqual(client.subprotocol, SUBPROTOCOL)
            await client.send(json.dumps({"protocolVersion": "1.0", "messageType": "request", "requestId": "status-1", "action": "getStatus", "params": {}}))
            response = json.loads(await client.recv())
            self.assertEqual(set(response), {"protocolVersion", "code", "data", "message"})
            self.assertEqual(response["code"], 200)
            self.assertEqual(response["data"]["result"]["gatewayBootId"], self.gateway.boot_id)

    async def test_binary_frame_is_rejected(self) -> None:
        async with websockets.connect(self.url, subprotocols=[SUBPROTOCOL], ping_interval=None, origin=self.origin) as client:
            await client.send(b"not-json")
            with self.assertRaises(websockets.exceptions.ConnectionClosedError) as error:
                await client.recv()
            self.assertEqual(error.exception.code, 1003)
            self.assertEqual(error.exception.reason, '请求格式不支持，请使用 JSON 文本发送')

    async def test_wrong_endpoint_is_closed_with_a_readable_chinese_reason(self) -> None:
        async with websockets.connect(self.url + '/wrong', subprotocols=[SUBPROTOCOL], ping_interval=None, origin=self.origin) as client:
            with self.assertRaises(websockets.exceptions.ConnectionClosedError) as error:
                await client.recv()
            self.assertEqual(error.exception.code, 1008)
            self.assertEqual(error.exception.reason, '连接地址错误，请检查网关地址和路径')

    async def test_business_error_reasons_are_readable_chinese_over_websocket(self) -> None:
        def request(action='getStatus', params=None, **changes):
            value={'protocolVersion':'1.0','messageType':'request','requestId':'chinese-error','action':action,'params':params or {}}
            value.update(changes)
            return json.dumps(value)
        cases=[
            ('{',400,'请求内容格式错误，请检查 JSON 格式后重试'),
            (request(extra=True),400,'请求信息不完整或包含多余字段，请检查后重试'),
            (request(messageType='response'),400,'请求类型、请求编号或参数格式不正确'),
            (request(protocolVersion='unsupported'),505,'通信协议版本不匹配，请使用网关支持的版本'),
            (request('unknown'),400,'当前操作不受支持，请检查操作名称'),
            (request(params={'streams':['eeg']}),400,'请求包含不支持的参数，请删除后重试'),
            (request('subscribe',{'streams':[]}),400,'数据类型填写错误，请至少选择一种数据类型'),
            (request('subscribe',{'streams':['status'],'includeInvalid':'false'}),400,'无效数据接收选项填写错误，请使用 true 或 false'),
            (request('unsubscribe'),400,'缺少有效的订阅编号，请先确认需要取消的订阅'),
            (request('unsubscribe',{'subscriptionId':'unknown'}),404,'订阅不存在或已失效，请检查订阅编号'),
            (request('getLatest',{'streams':['unknown']}),409,'请求的数据类型不受支持或暂不可用，请重新选择'),
            (request('getLatest',{'streams':['eeg']}),503,'头环未连接，且暂无可用的录播数据'),
        ]
        async with websockets.connect(self.url,subprotocols=[SUBPROTOCOL],ping_interval=None, origin=self.origin) as client:
            async def exchange(raw,code,message=None):
                await client.send(raw)
                result=json.loads(await asyncio.wait_for(client.recv(),timeout=1))
                self.assertEqual(result['code'],code,result)
                if message is not None:self.assertEqual(result['message'],message)
            for raw,code,message in cases:
                with self.subTest(message=message):await exchange(raw,code,message)
            self.gateway.config=replace(self.gateway.config,recording=replace(self.gateway.config.recording,replay_enabled=False))
            await exchange(request('getLatest',{'streams':['eeg']}),503,'头环未连接')
            self.gateway.status['connectionState']='connected'
            with patch.object(type(self.gateway.algorithm),'available',new_callable=PropertyMock,return_value=True):
                await exchange(request('subscribe',{'streams':['status']}),200)
                await exchange(request('subscribe',{'streams':['status']}),429,'该数据已在当前连接中订阅，请勿重复订阅')
                for stream in ['eeg.raw','hr.raw','eeg']:
                    await exchange(request('subscribe',{'streams':[stream]}),200)
                await exchange(request('subscribe',{'streams':['hr']}),429,'订阅数量已达上限，请先取消不需要的订阅')
            with patch.object(self.gateway,'status_result',side_effect=RuntimeError('synthetic failure')):
                await exchange(request(),500,'网关处理请求失败，请稍后重试或联系运维人员')

    async def test_local_browser_strategy_rejects_missing_or_unapproved_origins(self) -> None:
        for origin in (None, "http://localhost:8080", "https://example.com"):
            with self.subTest(origin=origin):
                with self.assertRaises(websockets.exceptions.InvalidStatusCode) as error:
                    await websockets.connect(self.url, subprotocols=[SUBPROTOCOL], origin=origin)
                self.assertEqual(error.exception.status_code, 403)
    async def test_corrupt_history_keeps_status_connected_and_healthy_replay_delivers(self) -> None:
        healthy = self.gateway.store.start(1000)
        self.gateway.store.save_algorithm(timestamp_ms=1200, valid=True, invalid_reasons=[], algorithm={"attention": 7})
        self.gateway.store.stop()
        broken = self.gateway.store.start(2000)
        self.gateway.store.stop()
        path = Path(self.directory.name) / "sessions" / broken / "raw/eeg.jsonl"
        path.write_text("\n", encoding="utf-8")
        async with websockets.connect(self.url, subprotocols=[SUBPROTOCOL], ping_interval=None, origin=self.origin) as client:
            async def request(action, params):
                await client.send(json.dumps({"protocolVersion": "1.0", "messageType": "request", "requestId": action, "action": action, "params": params}))
                return json.loads(await asyncio.wait_for(client.recv(), timeout=1))
            response = await request("getStatus", {})
            self.assertEqual(response["code"], 200)
            self.assertEqual(response["data"]["result"]["connectionState"], "disconnected")
            self.assertEqual(response["data"]["result"]["availableStreams"], ["status"])
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
        async with websockets.connect(self.url, subprotocols=[SUBPROTOCOL], ping_interval=None, origin=self.origin) as failed, websockets.connect(self.url, subprotocols=[SUBPROTOCOL], ping_interval=None, origin=self.origin) as healthy:
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
            self.assertEqual(failed.close_reason, '数据发送失败或接收过慢，请重新连接并订阅')
            self.assertTrue(self.gateway.live)
            async with websockets.connect(self.url, subprotocols=[SUBPROTOCOL], ping_interval=None, origin=self.origin) as restored:
                status = await request(restored, "getStatus", {})
                self.assertEqual(status["data"]["result"]["connectionState"], "connected")
                confirmation = await request(restored, "subscribe", {"streams": ["eeg.raw"]})
                self.assertNotEqual(confirmation["data"]["result"]["subscriptionId"], old_id)
                await self.gateway.publish_window(window)
                self.assertEqual(json.loads(await asyncio.wait_for(restored.recv(), timeout=1))["data"]["event"], "data")
