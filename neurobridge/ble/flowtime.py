from __future__ import annotations

import asyncio
import logging
import time
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

    def matching_candidates(self, devices: list[Any]) -> list[Any]:
        """Return profile matches without logging device addresses or advertisement bytes."""
        def matches(device: Any) -> bool:
            name_matches = self.config.device_name is None or getattr(device, "name", None) == self.config.device_name
            metadata = getattr(device, "metadata", {}) or {}
            advertised_uuids = {str(item).lower() for item in metadata.get("uuids", [])}
            return name_matches and self.config.model_nbr_uuid in advertised_uuids

        return [device for device in devices if matches(device)]

    def select_strongest(self, devices: list[Any]) -> Any | None:
        """Select the strongest advertisement matching the configured Flowtime profile."""
        candidates = self.matching_candidates(devices)
        return max(candidates, key=lambda device: getattr(device, "rssi", None) if isinstance(getattr(device, "rssi", None), int | float) else float("-inf"), default=None)

    async def run(self) -> None:
        if not self.config.enabled:
            LOG.info("Flowtime BLE adapter disabled by configuration")
            return
        from bleak import BleakClient, BleakScanner
        attempt = 0
        while not self._stopping:
            attempt += 1
            phase = "scan"
            try:
                await self.status("connectionState", "connecting")
                scan_started = time.monotonic()
                LOG.info(
                    "Flowtime scan started: attempt=%s timeoutSeconds=%s reconnectDelaySeconds=%s",
                    attempt,
                    self.config.scan_timeout_seconds,
                    self.config.reconnect_delay_seconds,
                )
                devices = await BleakScanner.discover(timeout=self.config.scan_timeout_seconds)
                candidates = self.matching_candidates(devices)
                candidate = self.select_strongest(candidates)
                LOG.info(
                    "Flowtime scan completed: attempt=%s discovered=%s matched=%s durationMs=%s",
                    attempt,
                    len(devices),
                    len(candidates),
                    int((time.monotonic() - scan_started) * 1000),
                )
                if candidate is None:
                    raise RuntimeError("No Flowtime headband matching the configured profile was found")
                phase = "connect"
                LOG.info("Connecting to Flowtime candidate: attempt=%s rssi=%s", attempt, getattr(candidate, "rssi", None))

                def disconnected(_: object) -> None:
                    LOG.warning("Flowtime disconnected callback received: attempt=%s", attempt)
                    asyncio.create_task(self.status("connectionState", "disconnected"))

                self._client = BleakClient(candidate, disconnected_callback=disconnected)
                await self._client.connect()
                LOG.info("Flowtime BLE link connected: attempt=%s", attempt)
                phase = "subscribe"
                await self._subscribe()
                phase = "algorithm_initialize"
                LOG.info("Flowtime notifications ready; initializing capture: attempt=%s", attempt)
                # Do not start device capture until the algorithm has a clean session.
                # This makes every post-FF21 packet eligible for automatic append.
                await self.device_ready()
                phase = "start_capture"
                await self._client.write_gatt_char(FF21, b"\x05", response=True)
                LOG.info("Flowtime start command acknowledged: attempt=%s command=0x05", attempt)
                # A connection is only published after all notifications, the FF21
                # start command, and the per-session algorithm initialization succeed.
                await self.status("connectionState", "connected")
                phase = "streaming"
                while self._client.is_connected and not self._stopping:
                    await asyncio.sleep(1)
                if not self._stopping:
                    await self.status("connectionState", "disconnected")
                    LOG.warning("Flowtime streaming ended without an exception: attempt=%s", attempt)
                    LOG.info(
                        "Flowtime reconnect scheduled: attempt=%s nextAttempt=%s delaySeconds=%s",
                        attempt,
                        attempt + 1,
                        self.config.reconnect_delay_seconds,
                    )
            except Exception as exc:
                LOG.exception(
                    "Flowtime connection failed: attempt=%s phase=%s errorType=%s",
                    attempt,
                    phase,
                    type(exc).__name__,
                )
                if self.error:
                    await self.error(str(exc))
                await self._disconnect_after_failure()
                await self.status("connectionState", "disconnected")
                LOG.info(
                    "Flowtime reconnect scheduled: attempt=%s nextAttempt=%s delaySeconds=%s",
                    attempt,
                    attempt + 1,
                    self.config.reconnect_delay_seconds,
                )
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
        LOG.info("Stopping Flowtime BLE adapter")
        if self._client and self._client.is_connected:
            try:
                await self._client.write_gatt_char(FF21, b"\x06", response=True)
                LOG.info("Flowtime stop command acknowledged: command=0x06")
                await self._client.disconnect()
                LOG.info("Flowtime BLE link disconnected during adapter stop")
            except Exception:
                LOG.exception("Failed to stop Flowtime collection")
