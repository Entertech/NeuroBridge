"""WebSocket-independent request parsing and use-case dispatch."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
import json
import logging
from typing import Any

from ...application.gateway import ClientSession, GatewayApplication, ProtocolError
from .protocol import envelope
from ...versioning import LEGACY_NORTHBOUND_PROTOCOL_VERSION, NORTHBOUND_PROTOCOL_VERSION


LOG = logging.getLogger(__name__)
DEVICE_SOURCES = {"bluetooth": "headband", "serial": "headset"}


def _versioned_message(message: dict, version: str, source: str) -> dict:
    data = message["data"]
    if version == NORTHBOUND_PROTOCOL_VERSION:
        data = {**data, "source": source}
    return {**message, "protocolVersion": version, "data": data}


class NorthboundController:
    """Keep wire request concerns outside device and application processing."""

    def __init__(self, gateway: GatewayApplication) -> None:
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
        if request["protocolVersion"] not in (NORTHBOUND_PROTOCOL_VERSION, LEGACY_NORTHBOUND_PROTOCOL_VERSION):
            raise ProtocolError(505, "UNSUPPORTED_VERSION", "Protocol version is not supported.")
        if (
            request["messageType"] != "request"
            or not isinstance(request["requestId"], str)
            or not isinstance(request["action"], str)
            or not isinstance(request["params"], dict)
        ):
            raise ProtocolError(400, "INVALID_REQUEST", "Request fields are invalid.")
        return request

    async def handle(self, session: ClientSession, raw: str | dict[str, Any], send: Any) -> None:
        self.gateway.delivery_metrics["requests"] += 1
        self.gateway.sessions.add(session)
        request_id: str | None = None
        action: str | None = None
        safe_request_id: str | None = None
        version = NORTHBOUND_PROTOCOL_VERSION
        source = DEVICE_SOURCES[self.gateway.config.data_source.type]

        async def send_versioned(message: dict) -> None:
            await send(_versioned_message(message, version, source))

        try:
            request = self.parse_request(raw) if isinstance(raw, str) else raw
            version = request["protocolVersion"]
            request_id = request["requestId"]
            action = request["action"]
            key = action if action in self.gateway.requests_by_action else "invalid"
            self.gateway.requests_by_action[key] += 1
            params = request["params"]
            if action in {"getStatus", "getLatest", "subscribe"}:
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
                result = await self.gateway.subscribe(session, params, send_versioned, start_replay=False)
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
            await send_versioned(envelope(200, {"requestId": request_id, "action": action, "result": result}))
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
            await send_versioned(self.gateway.error(request_id, error))
        except Exception:
            LOG.exception("Northbound request failed: action=%s requestId=%s", action, safe_request_id)
            await send_versioned(
                self.gateway.error(
                    request_id,
                    ProtocolError(500, "INTERNAL_ERROR", "Gateway request failed.", True),
                )
            )


class SourceRouter:
    """Route one WebSocket to independent device sessions and subscriptions."""

    def __init__(self, headset: NorthboundController, headband: NorthboundController) -> None:
        self.controllers = {"headset": headset, "headband": headband}

    @asynccontextmanager
    async def connection(self):
        sessions = {source: ClientSession() for source in self.controllers}
        async with AsyncExitStack() as cleanup:
            for source, controller in self.controllers.items():
                session = sessions[source]
                controller.gateway.sessions.add(session)
                cleanup.push_async_callback(controller.gateway.close_session, session)

            async def dispatch(raw: str, send: Any) -> None:
                headset = self.controllers["headset"]
                try:
                    request = headset.parse_request(raw)
                except ProtocolError:
                    await headset.handle(sessions["headset"], raw, send)
                    return
                version = request["protocolVersion"]
                params = request["params"].copy()
                source = params.pop("source", "headset") if version == NORTHBOUND_PROTOCOL_VERSION else "headset"
                if not isinstance(source, str) or source not in self.controllers:
                    error = ProtocolError(400, "INVALID_REQUEST", "params.source must be headset or headband.")
                    await send(_versioned_message(headset.gateway.error(request["requestId"], error), version, "headset"))
                    return
                await self.controllers[source].handle(sessions[source], {**request, "params": params}, send)

            yield dispatch
