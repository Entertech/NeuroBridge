from __future__ import annotations

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
        return HTTPStatus.UPGRADE_REQUIRED, [("Sec-WebSocket-Protocol", SUBPROTOCOL), ("Content-Type", "text/plain; charset=utf-8")], "连接协议不匹配，请使用 neurobridge.v1 子协议\n".encode("utf-8")
    return None


async def create_server(gateway: Gateway, controller=None):
    import websockets

    strategy = access_strategy(gateway.config.access.mode)
    strategy.validate(gateway.config)

    async def handler(websocket, path: str) -> None:
        if path != gateway.config.server.path:
            LOG.warning("WebSocket connection rejected: peer=%s endpoint=%s expected=%s", websocket.remote_address, path, gateway.config.server.path)
            await websocket.close(code=1008, reason="连接地址错误，请检查网关地址和路径")
            return
        session = ClientSession()
        async def close_failed_transport():
            await websocket.close(code=1011, reason="数据发送失败或接收过慢，请重新连接并订阅")
        session.close_transport = close_failed_transport
        send_lock = asyncio.Lock()
        gateway.sessions.add(session)
        peer = websocket.remote_address
        LOG.info("B-side WebSocket client connected: peer=%s activeClients=%s", peer, len(gateway.sessions))
        async def send(message: dict) -> None:
            async with send_lock:
                async with asyncio.timeout(gateway.config.pipeline.send_timeout_ms / 1000):
                    await websocket.send(json.dumps(message, separators=(",", ":"), ensure_ascii=False))
        try:
            async for message in websocket:
                if not isinstance(message, str):
                    LOG.warning("WebSocket binary frame rejected: peer=%s bytes=%s", peer, len(message))
                    await websocket.close(code=1003, reason="请求格式不支持，请使用 JSON 文本发送")
                    break
                LOG.debug("WebSocket text frame received: peer=%s bytes=%s", peer, len(message.encode("utf-8")))
                if controller is None:
                    await gateway.handle(session, message, send)
                else:
                    await controller.handle(session, message, send)
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


async def serve(gateway: Gateway, controller=None) -> None:
    server = await create_server(gateway, controller)
    try:
        LOG.info("Listening on ws://%s:%s%s", gateway.config.server.host, gateway.config.server.port, gateway.config.server.path)
        await asyncio.Future()
    finally:
        server.close()
        await server.wait_closed()
