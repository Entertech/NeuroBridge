"""WebSocket-independent request parsing and use-case dispatch."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ...application.gateway import ClientSession, GatewayApplication, ProtocolError
from .protocol import envelope
from ...versioning import NORTHBOUND_PROTOCOL_VERSION


LOG = logging.getLogger(__name__)


class NorthboundController:
    """Keep wire request concerns outside device and application processing."""

    def __init__(self, gateway: GatewayApplication) -> None:
        self.gateway = gateway

    @staticmethod
    def parse_request(raw: str) -> dict[str, Any]:
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ProtocolError(400, "INVALID_REQUEST", "请求内容格式错误，请检查 JSON 格式后重试") from error
        if not isinstance(request, dict) or set(request) != {
            "protocolVersion",
            "messageType",
            "requestId",
            "action",
            "params",
        }:
            raise ProtocolError(400, "INVALID_REQUEST", "请求信息不完整或包含多余字段，请检查后重试")
        if request["protocolVersion"] != NORTHBOUND_PROTOCOL_VERSION:
            raise ProtocolError(505, "UNSUPPORTED_VERSION", "通信协议版本不匹配，请使用网关支持的版本")
        if (
            request["messageType"] != "request"
            or not isinstance(request["requestId"], str)
            or not isinstance(request["action"], str)
            or not isinstance(request["params"], dict)
        ):
            raise ProtocolError(400, "INVALID_REQUEST", "请求类型、请求编号或参数格式不正确")
        return request

    async def handle(self, session: ClientSession, raw: str, send: Any) -> None:
        async def send_once(message):
            try:
                async with asyncio.timeout(min(self.gateway.config.pipeline.send_timeout_ms / 1000, self.gateway.config.server.send_timeout_seconds)):
                    await send(message)
            except Exception:
                self.gateway._fail_session(session)
                raise
        await self._handle(session, raw, send_once)

    async def _handle(self, session: ClientSession, raw: str, send: Any) -> None:
        self.gateway.delivery_metrics["requests"] += 1
        self.gateway.sessions.add(session)
        request_id: str | None = None
        action: str | None = None
        safe_request_id: str | None = None
        try:
            request = self.parse_request(raw)
            request_id = request["requestId"]
            action = request["action"]
            key = action if action in self.gateway.requests_by_action else "invalid"
            self.gateway.requests_by_action[key] += 1
            params = request["params"]
            status_only = isinstance(params.get("streams"), list) and params["streams"] and all(stream == "status" for stream in params["streams"])
            if action == "getLatest" or (action == "subscribe" and not status_only):
                await self.gateway.prepare_query()
            safe_request_id = request_id.replace("\n", " ").replace("\r", " ")[:96]
            start_replay = False
            if action == "getStatus":
                self.gateway.validate_params(params, set())
                result = self.gateway.status_result()
            elif action == "getLatest":
                self.gateway.validate_params(params, {"streams"})
                result = self.gateway.get_latest(session, params, start_replay=False)
                start_replay = not self.gateway.live and self.gateway.replay_available
            elif action == "subscribe":
                self.gateway.validate_params(params, {"streams", "includeInvalid"})
                result = await self.gateway.subscribe(session, params, send, start_replay=False)
                start_replay = (
                    any(stream != "status" for stream in result["streams"])
                    and not self.gateway.live
                    and self.gateway.replay_available
                )
            elif action == "unsubscribe":
                self.gateway.validate_params(params, {"subscriptionId"})
                result = await self.gateway.unsubscribe(session, params)
            else:
                raise ProtocolError(400, "INVALID_REQUEST", "当前操作不受支持，请检查操作名称", details={"action": action})
            response = envelope(200, {"requestId": request_id, "action": action, "result": result})
            if action == "getLatest":
                self.gateway.hrv_trace.envelope("latest_response", response,
                    hrRequested="hr" in params.get("streams", ["eeg", "hr"]), outcome="before_send")
            await send(response)
            LOG.info("Northbound request handled: action=%s requestId=%s code=200", action, safe_request_id)
            if start_replay:
                self.gateway._start_replay_if_needed()
        except ProtocolError as error:
            if action is None:
                self.gateway.requests_by_action["invalid"] += 1
            LOG.warning(
                "Northbound request rejected: action=%s requestId=%s code=%s reason=%s",
                action,
                safe_request_id,
                error.code,
                error.reason,
            )
            await send(self.gateway.error(request_id, error))
        except Exception:
            if session.failed:
                return
            LOG.exception("Northbound request failed: action=%s requestId=%s", action, safe_request_id)
            await send(
                self.gateway.error(
                    request_id,
                    ProtocolError(500, "INTERNAL_ERROR", "网关处理请求失败，请稍后重试或联系运维人员", True),
                )
            )
