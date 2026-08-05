from __future__ import annotations

import logging
from .gateway import ClientSession, Gateway

LOG = logging.getLogger(__name__)


async def serve(gateway: Gateway) -> None:
    import websockets

    async def handler(websocket, path: str) -> None:
        if path != gateway.config.server.path:
            await websocket.close(code=1008, reason="Unsupported endpoint")
            return
        session = ClientSession()
        gateway.sessions.add(session)
        async def send(message: dict) -> None:
            await websocket.send(__import__("json").dumps(message, separators=(",", ":"), ensure_ascii=False))
        try:
            async for message in websocket:
                if not isinstance(message, str):
                    await websocket.close(code=1003, reason="Text JSON required")
                    break
                await gateway.handle(session, message, send)
        finally:
            await gateway.close_session(session)

    async with websockets.serve(handler, gateway.config.server.host, gateway.config.server.port, subprotocols=["neurobridge.v1"], ping_interval=None, ping_timeout=None, max_size=256 * 1024, compression=None):
        LOG.info("Listening on ws://%s:%s%s", gateway.config.server.host, gateway.config.server.port, gateway.config.server.path)
        await __import__("asyncio").Future()
