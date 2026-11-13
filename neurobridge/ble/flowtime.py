from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import TYPE_CHECKING, Awaitable, Callable, Iterable

from ..config import BleConfig
from ..device.packet import DevicePacket

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

LOG = logging.getLogger(__name__)
BASE = "-1212-abcd-1523-785feabcd123"
FF31, FF32, FF51, FF21 = (f"0000{name}{BASE}" for name in ("ff31", "ff32", "ff51", "ff21"))
BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"
REQUIRED_NOTIFICATION_CHARACTERISTICS = (FF31, FF32, FF51)
OPTIONAL_NOTIFICATION_CHARACTERISTICS = (BATTERY,)


def wear_state_from_packet(_value: bytes) -> str:
    """FF32 state values require device-POC confirmation before interpretation."""
    return "unknown"


def _observe_winrt_session(client: object, attempt: int) -> Callable[[], None] | None:
    if sys.platform != "win32":
        return None

    # Bleak 0.19.5 drops the WinRT event args from its public disconnect callback.
    session = getattr(getattr(client, "_backend", None), "_session", None)

    def status_changed(_sender, args) -> None:
        try:
            status, error = args.status, args.error
            LOG.log(
                logging.WARNING if error else logging.INFO,
                "Flowtime WinRT GATT session status changed: attempt=%s status=%s(%d) bluetoothError=%s(%d)",
                attempt, status.name, int(status), error.name, int(error),
            )
        except Exception as exc:
            LOG.warning("Flowtime WinRT GATT session event could not be read: attempt=%s errorType=%s", attempt, type(exc).__name__)

    try:
        token = session.add_session_status_changed(status_changed)
    except Exception as exc:
        LOG.warning("Flowtime WinRT GATT session diagnostics unavailable: attempt=%s errorType=%s", attempt, type(exc).__name__)
        return None

    def remove_observer() -> None:
        try:
            session.remove_session_status_changed(token)
        except OSError:
            # Bleak can close the session before the streaming loop detects the drop.
            LOG.debug("Flowtime WinRT GATT observer already closed: attempt=%s", attempt)
        except Exception as exc:
            LOG.debug("Flowtime WinRT GATT observer cleanup failed: attempt=%s errorType=%s", attempt, type(exc).__name__)

    return remove_observer


class FlowtimeAdapter:
    """Bleak adapter for the FF31/FF32/FF51/FF21 profile in the device specification."""
    def __init__(self, config: BleConfig, packet: Callable[[DevicePacket], Awaitable[None]], status: Callable[[str, object], Awaitable[None]], device_ready: Callable[[], Awaitable[None]], error: Callable[[str], Awaitable[None]] | None = None, *, external_control: bool = False) -> None:
        self.config, self.packet, self.status, self.device_ready = config, packet, status, device_ready
        self.error = error
        self.external_control = external_control
        self._io_lock = asyncio.Lock()
        self._client = None
        self._stopping = False

    def matching_candidates(
        self, devices: Iterable[tuple[BLEDevice, AdvertisementData]]
    ) -> tuple[int, list[tuple[BLEDevice, AdvertisementData]]]:
        """Count advertised model matches and return candidates satisfying the optional filters."""
        model_uuid = self.config.model_nbr_uuid.lower()
        identifier = self.config.device_identifier
        model_matches = 0
        candidates = []
        for device, advertisement in devices:
            if not any(uuid.lower() == model_uuid for uuid in advertisement.service_uuids):
                continue
            model_matches += 1
            name = advertisement.local_name or device.name
            if self.config.device_name is not None and name != self.config.device_name:
                continue
            if identifier is not None and device.address.casefold() != identifier.casefold():
                continue
            candidates.append((device, advertisement))
        return model_matches, candidates

    async def run(self) -> None:
        if not self.config.enabled:
            LOG.info("Flowtime BLE adapter disabled by configuration")
            return
        from bleak import BleakClient, BleakScanner
        attempt = 0
        link_lost_at: float | None = None
        while not self._stopping:
            attempt += 1
            phase = "scan"
            remove_winrt_observer = None
            try:
                await self.status("connectionState", "connecting")
                scan_started = time.monotonic()
                LOG.info(
                    "Flowtime scan started: attempt=%s timeoutSeconds=%s reconnectDelaySeconds=%s",
                    attempt,
                    self.config.scan_timeout_seconds,
                    self.config.reconnect_delay_seconds,
                )
                devices = await BleakScanner.discover(timeout=self.config.scan_timeout_seconds, return_adv=True)
                if self._stopping:
                    return
                model_matches, candidates = self.matching_candidates(devices.values())
                LOG.info(
                    "Flowtime scan completed: attempt=%s discovered=%s modelMatches=%s matched=%s durationMs=%s",
                    attempt,
                    len(devices),
                    model_matches,
                    len(candidates),
                    int((time.monotonic() - scan_started) * 1000),
                )
                if not candidates:
                    if not devices:
                        LOG.warning("No BLE advertisements received; check the Bluetooth adapter and nearby devices")
                    elif not model_matches:
                        LOG.warning("BLE advertisements received, but none advertised the configured Flowtime model UUID")
                    else:
                        LOG.warning("Flowtime model UUID found, but no device matched the configured name or identifier")
                    raise RuntimeError("No Flowtime headband matching the configured profile was found")
                candidate, advertisement = max(candidates, key=lambda item: item[1].rssi)
                phase = "connect"
                connect_started = time.monotonic()
                disconnected_at: float | None = None
                last_notifications: dict[str, float] = {}
                LOG.info("Connecting to Flowtime candidate: attempt=%s rssi=%s", attempt, advertisement.rssi)

                def disconnected(_: object, callback_attempt: int = attempt) -> None:
                    nonlocal disconnected_at
                    if callback_attempt != attempt:
                        return
                    disconnected_at = time.monotonic()
                    LOG.log(
                        logging.INFO if self._stopping else logging.WARNING,
                        "Flowtime disconnected callback received: attempt=%s phase=%s stopping=%s",
                        attempt,
                        phase,
                        self._stopping,
                    )
                    asyncio.create_task(self.status("connectionState", "disconnected"))

                self._client = BleakClient(candidate, disconnected_callback=disconnected)
                await self._client.connect()
                connected_at = time.monotonic()
                remove_winrt_observer = _observe_winrt_session(self._client, attempt)
                LOG.info(
                    "Flowtime BLE link connected: attempt=%s durationMs=%s",
                    attempt,
                    int((connected_at - connect_started) * 1000),
                )
                phase = "subscribe"
                await self._subscribe(last_notifications)
                phase = "algorithm_initialize"
                LOG.info("Flowtime notifications ready; initializing capture: attempt=%s", attempt)
                # Do not start device capture until the algorithm has a clean session.
                # This makes every post-FF21 packet eligible for automatic append.
                ready = await self.device_ready()
                if ready is False:
                    raise ConnectionError("Local algorithm is not ready; BLE capture command was not sent")
                phase = "start_capture"
                if not self.external_control:
                    await self.write_control(b"\x05")
                LOG.info("Flowtime start command acknowledged: attempt=%s command=0x05", attempt)
                # A connection is only published after all notifications, the FF21
                # start command, and the per-session algorithm initialization succeed.
                phase = "streaming"
                await self.status("connectionState", "connected")
                if link_lost_at is not None:
                    LOG.info(
                        "Flowtime capture restarted: attempt=%s outageMs=%s",
                        attempt,
                        int((time.monotonic() - link_lost_at) * 1000),
                    )
                    link_lost_at = None
                while not self._stopping and self._client.is_connected:
                    await asyncio.sleep(1)
                if not self._stopping:
                    ended_at = disconnected_at if disconnected_at is not None else time.monotonic()
                    link_lost_at = ended_at
                    # The callback can precede the polling loop's observation of the link loss.
                    def notification_age_ms(characteristic: str) -> int | None:
                        received_at = last_notifications.get(characteristic)
                        return None if received_at is None else max(0, int((ended_at - received_at) * 1000))

                    LOG.warning(
                        "Flowtime streaming ended: attempt=%s disconnectCallback=%s connectedMs=%s "
                        "lastEegNotificationAgeMs=%s lastHrNotificationAgeMs=%s scanRssi=%s",
                        attempt,
                        disconnected_at is not None,
                        int((ended_at - connected_at) * 1000),
                        notification_age_ms(FF31),
                        notification_age_ms(FF51),
                        advertisement.rssi,
                    )
                    await self.status("connectionState", "disconnected")
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
            finally:
                if remove_winrt_observer is not None:
                    remove_winrt_observer()
            await asyncio.sleep(self.config.reconnect_delay_seconds)

    async def _disconnect_after_failure(self) -> None:
        """Do not leave a device-side BLE link open after incomplete subscription."""
        if self._client and self._client.is_connected:
            try:
                async with self._io_lock:
                    await self._client.disconnect()
            except Exception:
                LOG.exception("Failed to disconnect Flowtime after connection setup failure")
        self._client = None

    async def _subscribe(self, last_notifications: dict[str, float]) -> None:
        assert self._client
        async def notify(characteristic: str, value: bytearray) -> None:
            raw = bytes(value)
            if characteristic == FF32:
                await self.packet(DevicePacket.received("bluetooth", "ff32", raw))
                await self.status("wearState", wear_state_from_packet(raw))
            elif characteristic == BATTERY:
                # The profile's voltage-to-percent mapping still requires POC
                # validation. Do not expose the raw byte as a percentage.
                await self.status("batteryPercent", None)
            else:
                await self.packet(DevicePacket.received("bluetooth", {FF31: "ff31", FF51: "ff51"}[characteristic], raw))

        def handle_notification(characteristic: str, value: bytearray) -> None:
            if characteristic in (FF31, FF51):
                last_notifications[characteristic] = time.monotonic()
            asyncio.create_task(notify(characteristic, value))

        for characteristic in REQUIRED_NOTIFICATION_CHARACTERISTICS:
            # Bleak notification callbacks are synchronous on all supported backends.
            # Schedule the async state update rather than relying on backend-specific
            # coroutine callback behavior.
            await self._client.start_notify(characteristic, lambda _sender, value, c=characteristic: handle_notification(c, value))
        for characteristic in OPTIONAL_NOTIFICATION_CHARACTERISTICS:
            if not self._supports(characteristic):
                LOG.info("Optional Flowtime notification is not exposed by this headband; continuing without it")
                continue
            await self._client.start_notify(characteristic, lambda _sender, value, c=characteristic: handle_notification(c, value))
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

    async def write_control(self, command: bytes, *, session_valid=None) -> None:
        async with self._io_lock:
            if session_valid is not None and not session_valid():
                raise ConnectionError("BLE control belongs to an expired session")
            if self._client is None or not self._client.is_connected:
                raise ConnectionError("BLE session is no longer connected")
            await self._client.write_gatt_char(FF21, command, response=True)

    async def stop(self) -> None:
        self._stopping = True
        LOG.info("Stopping Flowtime BLE adapter")
        try:
            if not self.external_control and self._client and self._client.is_connected:
                await self.write_control(b"\x06")
        except Exception:
            LOG.exception("Failed to stop Flowtime collection")
        finally:
            await self._disconnect_after_failure()
