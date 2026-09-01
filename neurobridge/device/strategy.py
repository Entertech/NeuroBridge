"""Resolve one device transport without leaking transport branches into Gateway."""

from __future__ import annotations

from typing import Protocol

from ..ble.flowtime import FlowtimeAdapter
from ..business.gateway import Gateway
from ..config import GatewayConfig


class DeviceAdapter(Protocol):
    async def run(self) -> None: ...

    async def stop(self) -> None: ...


def create_device_adapter(config: GatewayConfig, gateway: Gateway) -> DeviceAdapter:
    """Create the configured adapter from the extensible device strategy registry."""

    if config.data_source.type == "bluetooth":
        return FlowtimeAdapter(
            config.ble,
            gateway.receive_packet,
            gateway.update_status,
            gateway.on_device_ready,
            gateway.update_connection_error,
        )
    if config.data_source.type == "serial":
        from ..serial.adapter import SerialAdapter

        return SerialAdapter(
            config.serial,
            gateway.receive_packet,
            gateway.update_status,
            gateway.on_device_ready,
            gateway.update_connection_error,
        )
    raise ValueError(f"Unsupported data_source.type strategy: {config.data_source.type}")
