from __future__ import annotations

from ...ports.northbound import ApplicationEvent


class CollectingNorthboundSink:
    """Small sink useful for composition and contract tests."""

    def __init__(self) -> None:
        self.events: list[ApplicationEvent] = []

    async def publish(self, event: ApplicationEvent) -> None:
        self.events.append(event)
