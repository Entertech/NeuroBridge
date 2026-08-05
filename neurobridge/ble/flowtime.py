from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable
from ..config import BleConfig

LOG = logging.getLogger(__name__)
BASE = "-1212-abcd-1523-785feabcd123"
FF31, FF32, FF51, FF52, FF21 = (f"0000{name}{BASE}" for name in ("ff31", "ff32", "ff51", "ff52", "ff21"))
BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"


class FlowtimeAdapter:
    """Bleak adapter modeled on the PC SDK, with the confirmed v0.1 byte contract."""
    def __init__(self, config: BleConfig, packet: Callable[[str, bytes], Awaitable[None]], status: Callable[[str, object], Awaitable[None]]) -> None:
        self.config, self.packet, self.status = config, packet, status
        self._client = None
        self._stopping = False
        self._address = config.device_address

    async def run(self) -> None:
        if not self.config.enabled:
            return
        from bleak import BleakClient, BleakScanner
        while not self._stopping:
            try:
                await self.status("connectionState", "connecting")
                if not self._address:
                    LOG.info("Scanning for Flowtime headband")
                    devices = await BleakScanner.discover(timeout=self.config.scan_timeout_seconds)
                    candidate = next((device for device in devices if self.config.device_name is None or device.name == self.config.device_name), None)
                    if candidate is None:
                        raise RuntimeError("Configured Flowtime headband was not found")
                    self._address = candidate.address
                self._client = BleakClient(self._address, disconnected_callback=lambda _: asyncio.create_task(self.status("connectionState", "disconnected")))
                await self._client.connect()
                await self._subscribe()
                await self.status("connectionState", "connected")
                while self._client.is_connected and not self._stopping:
                    await asyncio.sleep(1)
            except Exception:
                LOG.exception("Flowtime connection failed")
                await self.status("connectionState", "disconnected")
            await asyncio.sleep(self.config.reconnect_delay_seconds)

    async def _subscribe(self) -> None:
        assert self._client
        async def notify(characteristic: str, _sender: int, value: bytearray) -> None:
            if characteristic == FF32:
                await self.status("wearState", "worn" if value == b"\x00\x00" else ("notWorn" if len(value) == 2 else "unknown"))
            elif characteristic == BATTERY:
                # The profile's voltage-to-percent mapping still requires POC
                # validation. Do not expose the raw byte as a percentage.
                await self.status("batteryPercent", None)
            else:
                await self.packet({FF31: "ff31", FF51: "ff51", FF52: "ff52"}[characteristic], bytes(value))
        for characteristic in (FF31, FF32, FF51, FF52, BATTERY):
            # Bleak notification callbacks are synchronous on all supported backends.
            # Schedule the async state update rather than relying on backend-specific
            # coroutine callback behavior.
            await self._client.start_notify(characteristic, lambda sender, value, c=characteristic: asyncio.create_task(notify(c, sender, value)))
        await self._client.write_gatt_char(FF21, b"\x05", response=True)

    async def stop(self) -> None:
        self._stopping = True
        if self._client and self._client.is_connected:
            try:
                await self._client.write_gatt_char(FF21, b"\x06", response=True)
                await self._client.disconnect()
            except Exception:
                LOG.exception("Failed to stop Flowtime collection")
