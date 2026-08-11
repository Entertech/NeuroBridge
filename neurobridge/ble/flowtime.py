from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable
from ..config import BleConfig

LOG = logging.getLogger(__name__)
BASE = "-1212-abcd-1523-785feabcd123"
FF31, FF32, FF51, FF21 = (f"0000{name}{BASE}" for name in ("ff31", "ff32", "ff51", "ff21"))
BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"
REQUIRED_NOTIFICATION_CHARACTERISTICS = (FF31, FF32, FF51)
OPTIONAL_NOTIFICATION_CHARACTERISTICS = (BATTERY,)


def wear_state_from_packet(_value: bytes) -> str:
    """FF32 state values require device-POC confirmation before interpretation."""
    return "unknown"


class FlowtimeAdapter:
    """Bleak adapter for the FF31/FF32/FF51/FF21 profile in the device specification."""
    def __init__(self, config: BleConfig, packet: Callable[[str, bytes], Awaitable[None]], status: Callable[[str, object], Awaitable[None]], device_ready: Callable[[], Awaitable[None]], error: Callable[[str], Awaitable[None]] | None = None) -> None:
        self.config, self.packet, self.status, self.device_ready = config, packet, status, device_ready
        self.error = error
        self._client = None
        self._stopping = False

    def select_strongest(self, devices: list[Any]) -> Any | None:
        """Select the strongest advertisement matching the configured UUID.

        Device names are intentionally not used: they are mutable presentation
        data and are not a stable part of the confirmed device profile.
        """
        def matches(device: Any) -> bool:
            metadata = getattr(device, "metadata", {}) or {}
            advertised_uuids = {str(item).lower() for item in metadata.get("uuids", [])}
            return self.config.model_nbr_uuid in advertised_uuids

        candidates = [device for device in devices if matches(device)]
        return max(candidates, key=lambda device: getattr(device, "rssi", None) if isinstance(getattr(device, "rssi", None), int | float) else float("-inf"), default=None)

    async def run(self) -> None:
        if not self.config.enabled:
            return
        from bleak import BleakClient, BleakScanner
        while not self._stopping:
            try:
                await self.status("connectionState", "connecting")
                LOG.info("Scanning for Flowtime headband by configured profile UUID")
                devices = await BleakScanner.discover(timeout=self.config.scan_timeout_seconds)
                candidate = self.select_strongest(devices)
                if candidate is None:
                    raise RuntimeError("No Flowtime headband advertising the configured profile UUID was found")
                LOG.info("Connecting to Flowtime candidate (RSSI=%s)", getattr(candidate, "rssi", None))
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
            except Exception as exc:
                LOG.exception("Flowtime connection failed")
                if self.error:
                    await self.error(str(exc))
                await self._disconnect_after_failure()
                await self.status("connectionState", "disconnected")
            await asyncio.sleep(self.config.reconnect_delay_seconds)

    async def _disconnect_after_failure(self) -> None:
        """Do not leave a device-side BLE link open after incomplete subscription."""
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except Exception:
                LOG.exception("Failed to disconnect Flowtime after connection setup failure")
        self._client = None

    async def _subscribe(self) -> None:
        assert self._client
        async def notify(characteristic: str, _sender: int, value: bytearray) -> None:
            if characteristic == FF32:
                await self.status("wearState", wear_state_from_packet(bytes(value)))
            elif characteristic == BATTERY:
                # The profile's voltage-to-percent mapping still requires POC
                # validation. Do not expose the raw byte as a percentage.
                await self.status("batteryPercent", None)
            else:
                await self.packet({FF31: "ff31", FF51: "ff51"}[characteristic], bytes(value))
        for characteristic in REQUIRED_NOTIFICATION_CHARACTERISTICS:
            # Bleak notification callbacks are synchronous on all supported backends.
            # Schedule the async state update rather than relying on backend-specific
            # coroutine callback behavior.
            await self._client.start_notify(characteristic, lambda sender, value, c=characteristic: asyncio.create_task(notify(c, sender, value)))
        for characteristic in OPTIONAL_NOTIFICATION_CHARACTERISTICS:
            if not self._supports(characteristic):
                LOG.info("Optional Flowtime notification is not exposed by this headband; continuing without it")
                continue
            await self._client.start_notify(characteristic, lambda sender, value, c=characteristic: asyncio.create_task(notify(c, sender, value)))
        LOG.info("Subscribed to required Flowtime EEG, wear-state, and heart-rate notifications")

    def _supports(self, characteristic: str) -> bool:
        """Return whether a post-connect GATT characteristic is available.

        Optional telemetry must never make an otherwise complete capture setup fail.
        """
        assert self._client
        try:
            return self._client.services.get_characteristic(characteristic) is not None
        except Exception:
            LOG.warning("Could not inspect optional Flowtime notification support; continuing without it")
            return False

    async def stop(self) -> None:
        self._stopping = True
        if self._client and self._client.is_connected:
            try:
                await self._client.write_gatt_char(FF21, b"\x06", response=True)
                await self._client.disconnect()
            except Exception:
                LOG.exception("Failed to stop Flowtime collection")
