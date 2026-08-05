from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable
from ..config import BleConfig

LOG = logging.getLogger(__name__)
BASE = "-1212-abcd-1523-785feabcd123"
FF31, FF32, FF51, FF52, FF21 = (f"0000{name}{BASE}" for name in ("ff31", "ff32", "ff51", "ff52", "ff21"))
BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"


class FlowtimeAdapter:
    """Bleak adapter modeled on the PC SDK, with the confirmed v0.1 byte contract."""
    def __init__(self, config: BleConfig, packet: Callable[[str, bytes], Awaitable[None]], status: Callable[[str, object], Awaitable[None]], device_ready: Callable[[], Awaitable[None]]) -> None:
        self.config, self.packet, self.status, self.device_ready = config, packet, status, device_ready
        self._client = None
        self._stopping = False

    def select_strongest(self, devices: list[Any]) -> Any | None:
        """Select the strongest advertisement matching the configured Flowtime profile."""
        def matches(device: Any) -> bool:
            name_matches = self.config.device_name is None or getattr(device, "name", None) == self.config.device_name
            metadata = getattr(device, "metadata", {}) or {}
            advertised_uuids = {str(item).lower() for item in metadata.get("uuids", [])}
            return name_matches and self.config.model_nbr_uuid in advertised_uuids

        candidates = [device for device in devices if matches(device)]
        return max(candidates, key=lambda device: getattr(device, "rssi", None) if isinstance(getattr(device, "rssi", None), int | float) else float("-inf"), default=None)

    async def run(self) -> None:
        if not self.config.enabled:
            return
        from bleak import BleakClient, BleakScanner
        while not self._stopping:
            try:
                await self.status("connectionState", "connecting")
                LOG.info("Scanning for Flowtime headband")
                devices = await BleakScanner.discover(timeout=self.config.scan_timeout_seconds)
                candidate = self.select_strongest(devices)
                if candidate is None:
                    raise RuntimeError("No Flowtime headband matching the configured profile was found")
                LOG.info("Connecting to Flowtime candidate %s (RSSI=%s)", candidate.address, getattr(candidate, "rssi", None))
                self._client = BleakClient(candidate, disconnected_callback=lambda _: asyncio.create_task(self.status("connectionState", "disconnected")))
                await self._client.connect()
                await self._subscribe()
                # Do not start device capture until the algorithm has a clean session.
                # This makes every post-FF21 packet eligible for automatic append.
                await self.device_ready()
                await self._client.write_gatt_char(FF21, b"\x05", response=True)
                # A connection is only published after all notifications, the FF21
                # start command, and the per-session algorithm initialization succeed.
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

    async def stop(self) -> None:
        self._stopping = True
        if self._client and self._client.is_connected:
            try:
                await self._client.write_gatt_char(FF21, b"\x06", response=True)
                await self._client.disconnect()
            except Exception:
                LOG.exception("Failed to stop Flowtime collection")
