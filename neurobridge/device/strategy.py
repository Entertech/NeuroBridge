"""Resolve one device transport without leaking transport branches into Gateway."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from ..business.gateway import Gateway
from ..config import GatewayConfig


class DeviceAdapter(Protocol):
    async def run(self) -> None: ...

    async def stop(self) -> None: ...


def _bluetooth(config: GatewayConfig, gateway: Gateway) -> DeviceAdapter:
    from ..ble.flowtime import FlowtimeAdapter

    return FlowtimeAdapter(config.ble, gateway.receive_device_packet, gateway.update_status, gateway.on_device_ready, gateway.update_connection_error)


def _serial(config: GatewayConfig, gateway: Gateway) -> DeviceAdapter:
    from ..serial.adapter import SerialAdapter

    async def prepare_algorithm():
        await asyncio.wait_for(gateway.algorithm.initialize(), config.algorithm.request_timeout_ms / 1000)
        return gateway.algorithm.available

    async def activate_algorithm():
        return await gateway.on_device_ready(already_prepared=True)

    return SerialAdapter(config.serial, gateway.receive_device_packet, gateway.update_status,
                         activate_algorithm, gateway.update_connection_error,
                         prepare_algorithm=prepare_algorithm, release_algorithm=gateway.algorithm.stop)


def _unconfirmed_native_usb(_config: GatewayConfig, _gateway: Gateway) -> DeviceAdapter:
    raise NotImplementedError(
        "data_source.type=usb is reserved for native USB, but the device VID/PID, "
        "interface, endpoint, transfer type, and Linux driver contract are not confirmed; "
        "use data_source.type=serial when USB enumerates as /dev/ttyACM* or /dev/ttyUSB*"
    )


_STRATEGIES: dict[str, Callable[[GatewayConfig, Gateway], DeviceAdapter]] = {
    "bluetooth": _bluetooth,
    "serial": _serial,
    "usb": _unconfirmed_native_usb,
}


def create_device_adapter(config: GatewayConfig, gateway: Gateway) -> DeviceAdapter:
    """Create exactly one configured adapter from the strategy registry."""

    try:
        factory = _STRATEGIES[config.data_source.type]
    except KeyError as exc:
        raise ValueError(f"Unsupported data_source.type strategy: {config.data_source.type}") from exc
    return factory(config, gateway)
