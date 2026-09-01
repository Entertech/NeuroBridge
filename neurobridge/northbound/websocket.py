from __future__ import annotations

from http import HTTPStatus
import asyncio
import json
import logging

from ..business.gateway import ClientSession, Gateway
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


async def create_server(gateway: Gateway):
    import websockets

    strategy = access_strategy(gateway.config.access.mode)
    strategy.validate(gateway.config)

    async def handler(websocket, path: str) -> None:
        if path != gateway.config.server.path:
            LOG.warning("WebSocket connection rejected: peer=%s endpoint=%s expected=%s", websocket.remote_address, path, gateway.config.server.path)
            await websocket.close(code=1008, reason="Unsupported endpoint")
            return
        session = ClientSession()
        gateway.sessions.add(session)
        peer = websocket.remote_address
        LOG.info("B-side WebSocket client connected: peer=%s activeClients=%s", peer, len(gateway.sessions))
        async def send(message: dict) -> None:
            await websocket.send(json.dumps(message, separators=(",", ":"), ensure_ascii=False))
        try:
            async for message in websocket:
                if not isinstance(message, str):
                    LOG.warning("WebSocket binary frame rejected: peer=%s bytes=%s", peer, len(message))
                    await websocket.close(code=1003, reason="Text JSON required")
                    break
                LOG.debug("WebSocket text frame received: peer=%s bytes=%s", peer, len(message.encode("utf-8")))
                await gateway.handle(session, message, send)
        finally:
            await gateway.close_session(session)
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


async def serve(gateway: Gateway) -> None:
    server = await create_server(gateway)
    try:
        LOG.info("Listening on ws://%s:%s%s", gateway.config.server.host, gateway.config.server.port, gateway.config.server.path)
        await asyncio.Future()
    finally:
        server.close()
        await server.wait_closed()
