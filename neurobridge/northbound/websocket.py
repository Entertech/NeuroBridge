from __future__ import annotations

from http import HTTPStatus
import asyncio
import json
import logging
from ..business.gateway import ClientSession, Gateway

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
    from websockets.exceptions import ConnectionClosed

    async def handler(websocket, path: str) -> None:
        if path != gateway.config.server.path:
            await websocket.close(code=1008, reason="Unsupported endpoint")
            return
        session = ClientSession()
        async def close_failed_transport() -> None:
            await websocket.close(code=1011, reason="Delivery failed; reconnect and resubscribe")
        session.close_transport = close_failed_transport
        gateway.sessions.add(session)
        peer = websocket.remote_address
        send_lock = asyncio.Lock()
        LOG.info("B-side WebSocket client connected: bootId=%s sessionId=%s peer=%s local=%s", gateway.boot_id, session.id, peer, websocket.local_address)
        async def send(message: dict) -> None:
            async with send_lock:
                await websocket.send(json.dumps(message, separators=(",", ":"), ensure_ascii=False))
        try:
            async for message in websocket:
                if not isinstance(message, str):
                    await websocket.close(code=1003, reason="Text JSON required")
                    break
                await gateway.handle(session, message, send)
        except ConnectionClosed as exc:
            LOG.info("B-side WebSocket connection closed: bootId=%s sessionId=%s closeCode=%s", gateway.boot_id, session.id, exc.code)
        except OSError as exc:
            LOG.warning("B-side WebSocket receive failed: bootId=%s sessionId=%s errorType=%s errno=%s", gateway.boot_id, session.id, type(exc).__name__, exc.errno)
        finally:
            await gateway.close_session(session)
            LOG.info("B-side WebSocket client disconnected: bootId=%s sessionId=%s peer=%s closeCode=%s deliveryFailed=%s", gateway.boot_id, session.id, peer, websocket.close_code, session.failed)

    return await websockets.serve(handler, gateway.config.server.host, gateway.config.server.port, subprotocols=[SUBPROTOCOL], process_request=require_subprotocol, ping_interval=None, ping_timeout=None, max_size=256 * 1024, compression=None)


async def serve(gateway: Gateway) -> None:
    try:
        server = await create_server(gateway)
    except OSError as exc:
        LOG.error("WebSocket listener bind failed: bootId=%s host=%s port=%s errorType=%s errno=%s", gateway.boot_id, gateway.config.server.host, gateway.config.server.port, type(exc).__name__, exc.errno)
        raise
    try:
        LOG.info("Listening on ws://%s:%s%s", gateway.config.server.host, gateway.config.server.port, gateway.config.server.path)
        await asyncio.Future()
    finally:
        server.close()
        await server.wait_closed()
