"""Deployment access strategies for the shared northbound WebSocket contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
import ipaddress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import GatewayConfig


class AccessStrategy(ABC):
    """Vary exposure policy without branching gateway business behavior."""

    mode: str

    @abstractmethod
    def validate(self, config: GatewayConfig) -> None:
        """Reject a topology that is unsafe or inconsistent for this strategy."""

    @abstractmethod
    def websocket_origins(self, config: GatewayConfig) -> tuple[str, ...] | None:
        """Return browser origins accepted by websockets, or None for legacy clients."""

    @abstractmethod
    def serves_local_ui(self, config: GatewayConfig) -> bool:
        """Whether the gateway should start the loopback static UI service."""

    @abstractmethod
    def summary(self, config: GatewayConfig) -> str:
        """Return a credential-free operational summary."""


class LocalBrowserAccessStrategy(AccessStrategy):
    mode = "local_browser"

    def validate(self, config: GatewayConfig) -> None:
        if config.server.host != "127.0.0.1":
            raise ValueError("local_browser requires server.host = 127.0.0.1")
        if config.download.enabled and config.download.host != "127.0.0.1":
            raise ValueError("local_browser requires download.host = 127.0.0.1 when downloads are enabled")
        if not config.local_ui.enabled:
            raise ValueError("local_browser requires local_ui.enabled = true")
        if config.local_ui.host != "127.0.0.1":
            raise ValueError("local_browser requires local_ui.host = 127.0.0.1")
        if config.local_ui.port == config.server.port:
            raise ValueError("local_ui.port must differ from server.port")
        if config.download.enabled and config.local_ui.port == config.download.port:
            raise ValueError("local_ui.port must differ from download.port when downloads are enabled")
        if config.network.mode != "static":
            raise ValueError("local_browser does not use the dedicated-link DHCP service")
        if any(
            value is not None
            for value in (
                config.network.interface,
                config.network.subnet_cidr,
                config.network.dhcp_range_start,
                config.network.dhcp_range_end,
            )
        ):
            raise ValueError("local_browser must not configure dedicated B-side network or DHCP fields")

    def websocket_origins(self, config: GatewayConfig) -> tuple[str, ...]:
        return (f"http://{config.local_ui.host}:{config.local_ui.port}",)

    def serves_local_ui(self, config: GatewayConfig) -> bool:
        return config.local_ui.enabled

    def summary(self, config: GatewayConfig) -> str:
        return f"loopbackUi=http://{config.local_ui.host}:{config.local_ui.port}/"


class WiredBSideAccessStrategy(AccessStrategy):
    mode = "wired_b_side"

    def validate(self, config: GatewayConfig) -> None:
        address = ipaddress.ip_address(config.server.host)
        if address.is_loopback or not address.is_private:
            raise ValueError("wired_b_side requires server.host to be a non-loopback private IP address")
        if config.local_ui.enabled:
            raise ValueError("wired_b_side requires local_ui.enabled = false")
        if config.download.enabled:
            download_address = ipaddress.ip_address(config.download.host)
            if download_address.is_loopback or not download_address.is_private:
                raise ValueError("wired_b_side requires download.host to be a non-loopback private IP address")

    def websocket_origins(self, config: GatewayConfig) -> None:
        # Preserve compatibility with non-browser clients and the published
        # dedicated-link contract. The network itself remains isolated.
        return None

    def serves_local_ui(self, config: GatewayConfig) -> bool:
        return False

    def summary(self, config: GatewayConfig) -> str:
        return "dedicatedWiredClient=true"


_STRATEGIES: dict[str, AccessStrategy] = {
    LocalBrowserAccessStrategy.mode: LocalBrowserAccessStrategy(),
    WiredBSideAccessStrategy.mode: WiredBSideAccessStrategy(),
}


def access_strategy(mode: str) -> AccessStrategy:
    """Resolve the selected strategy from one extensible registry."""

    try:
        return _STRATEGIES[mode]
    except KeyError as exc:
        raise ValueError(f"Unsupported access.mode strategy: {mode}") from exc
