from __future__ import annotations

from ...ports.northbound import ApplicationEvent


class CollectingNorthboundSink:
    """Small sink useful for composition and contract tests."""

    def __init__(self) -> None:
        self.events: list[ApplicationEvent] = []

    async def publish(self, event: ApplicationEvent) -> None:
        self.events.append(event)


class GatewayNorthboundSink:
    """Publish application events through the wire-compatible query/subscription service."""

    def __init__(self, gateway: object) -> None:
        self.gateway = gateway

    async def publish(self, event: ApplicationEvent) -> None:
        if event.kind != "window" or event.result is None:
            return
        if event.algorithm_state is not None:
            await self.gateway.update_status("algorithmState", event.algorithm_state.value)
        publish = getattr(self.gateway, "publish_window_result")
        await publish(event.result)
