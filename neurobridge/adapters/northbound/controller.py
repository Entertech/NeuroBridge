"""WebSocket-independent request parsing and use-case dispatch."""

from __future__ import annotations

import json
import logging
from typing import Any

from ...business.gateway import ClientSession, Gateway, ProtocolError, envelope
from ...versioning import NORTHBOUND_PROTOCOL_VERSION


LOG = logging.getLogger(__name__)


class NorthboundController:
    """Keep wire request concerns outside device and application processing."""

    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway

    @staticmethod
    def parse_request(raw: str) -> dict[str, Any]:
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ProtocolError(400, "INVALID_REQUEST", "Request is not valid JSON.") from error
        if not isinstance(request, dict) or set(request) != {
            "protocolVersion",
            "messageType",
            "requestId",
            "action",
            "params",
        }:
            raise ProtocolError(400, "INVALID_REQUEST", "Request root fields are invalid.")
        if request["protocolVersion"] != NORTHBOUND_PROTOCOL_VERSION:
            raise ProtocolError(505, "UNSUPPORTED_VERSION", "Protocol version is not supported.")
        if (
            request["messageType"] != "request"
            or not isinstance(request["requestId"], str)
            or not isinstance(request["action"], str)
            or not isinstance(request["params"], dict)
        ):
            raise ProtocolError(400, "INVALID_REQUEST", "Request fields are invalid.")
        return request

    async def handle(self, session: ClientSession, raw: str, send: Any) -> None:
        self.gateway.sessions.add(session)
        request_id: str | None = None
        action: str | None = None
        safe_request_id: str | None = None
        try:
            request = self.parse_request(raw)
            request_id = request["requestId"]
            action = request["action"]
            params = request["params"]
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
                raise ProtocolError(400, "INVALID_REQUEST", "Unknown action.", details={"action": action})
            await send(envelope(200, {"requestId": request_id, "action": action, "result": result}))
            LOG.info("Northbound request handled: action=%s requestId=%s code=200", action, safe_request_id)
            if start_replay:
                self.gateway._start_replay_if_needed()
        except ProtocolError as error:
            LOG.warning(
                "Northbound request rejected: action=%s requestId=%s code=%s reason=%s",
                action,
                safe_request_id,
                error.code,
                error.reason,
            )
            await send(self.gateway.error(request_id, error))
        except Exception:
            LOG.exception("Northbound request failed: action=%s requestId=%s", action, safe_request_id)
            await send(
                self.gateway.error(
                    request_id,
                    ProtocolError(500, "INTERNAL_ERROR", "Gateway request failed.", True),
                )
            )
