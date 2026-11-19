from __future__ import annotations

from contextlib import AsyncExitStack
from http import HTTPStatus
import asyncio
import json
import logging

from ..application.gateway import ClientSession, GatewayApplication as Gateway
from .strategy import access_strategy

LOG = logging.getLogger(__name__)
SUBPROTOCOL = "neurobridge.v1"


async def require_subprotocol(_path: str, request_headers):
    offered = {
        item.strip()
        for header in request_headers.get_all("Sec-WebSocket-Protocol")
        for item in header.split(",")
    }
    if SUBPROTOCOL not in offered:
        return HTTPStatus.UPGRADE_REQUIRED, [("Sec-WebSocket-Protocol", SUBPROTOCOL)], b"WebSocket subprotocol neurobridge.v1 is required.\n"
    return None


async def create_server(gateway: Gateway, controller=None, *, router=None):
    import websockets

    strategy = access_strategy(gateway.config.access.mode)
    strategy.validate(gateway.config)

    async def handler(websocket, path: str) -> None:
        if path != gateway.config.server.path:
            LOG.warning("WebSocket connection rejected: peer=%s endpoint=%s expected=%s", websocket.remote_address, path, gateway.config.server.path)
            await websocket.close(code=1008, reason="Unsupported endpoint")
            return
        peer = websocket.remote_address
        async def send(message: dict) -> None:
            await websocket.send(json.dumps(message, separators=(",", ":"), ensure_ascii=False))
        try:
            async with AsyncExitStack() as cleanup:
                if router is None:
                    session = ClientSession()
                    gateway.sessions.add(session)
                    cleanup.push_async_callback(gateway.close_session, session)
                    handler = controller or gateway

                    async def dispatch(message, send):
                        await handler.handle(session, message, send)
                else:
                    dispatch = await cleanup.enter_async_context(router.connection())

                LOG.info("B-side WebSocket client connected: peer=%s activeClients=%s", peer, len(gateway.sessions))
                async for message in websocket:
                    if not isinstance(message, str):
                        LOG.warning("WebSocket binary frame rejected: peer=%s bytes=%s", peer, len(message))
                        await websocket.close(code=1003, reason="Text JSON required")
                        break
                    LOG.debug("WebSocket text frame received: peer=%s bytes=%s", peer, len(message.encode("utf-8")))
                    await dispatch(message, send)
        finally:
            close_reason = websocket.close_reason or ""
            safe_close_reason = "".join(
                character if character.isprintable() else " " for character in close_reason
            )[:256]
            LOG.info(
                "B-side WebSocket client disconnected: peer=%s closeCode=%s closeReason=%s activeClients=%s",
                peer,
                websocket.close_code,
                safe_close_reason,
                len(gateway.sessions),
            )

    return await websockets.serve(
        handler,
        gateway.config.server.host,
        gateway.config.server.port,
        subprotocols=[SUBPROTOCOL],
        process_request=require_subprotocol,
        origins=strategy.websocket_origins(gateway.config),
        ping_interval=None,
        ping_timeout=None,
        max_size=256 * 1024,
        compression=None,
    )


async def serve(gateway: Gateway, controller=None, *, router=None) -> None:
    server = await create_server(gateway, controller, router=router)
    try:
        LOG.info("Listening on ws://%s:%s%s", gateway.config.server.host, gateway.config.server.port, gateway.config.server.path)
        await asyncio.Future()
    finally:
        server.close()
        await server.wait_closed()
