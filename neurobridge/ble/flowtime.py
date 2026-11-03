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


def wear_state_from_packet(value: bytes) -> str:
    """Device protocol table 4-4: EEG/HR zero means attached/good contact."""
    if len(value) != 2:
        return "unknown"
    return "worn" if value == b"\x00\x00" else "notWorn"


def battery_percent_from_packet(value: bytes) -> int | None:
    """Table 4-2: 3.1 V = 0%, 4.1 V = 100%; 0x52 = 3.92 V (82%)."""
    return value[0] if len(value) == 1 and value[0] <= 100 else None


class FlowtimeAdapter:
    """Bleak adapter for the FF31/FF32/FF51/FF21 profile in the device specification."""
    def __init__(self, config: BleConfig, packet: Callable[[str, bytes], Awaitable[None]], status: Callable[[str, object], Awaitable[None]], device_ready: Callable[[], Awaitable[None]], error: Callable[[str], Awaitable[None]] | None = None, diagnostics_interval_seconds: float = 30) -> None:
        self.config, self.packet, self.status, self.device_ready = config, packet, status, device_ready
        self.error = error
        self._client = None
        self._stopping = False
        self._attempt = 0
        self._disconnected = asyncio.Event()
        self._callback_tasks: set[asyncio.Task] = set()
        self._connected_at: float | None = None
        self._last_packet_at: float | None = None
        self._packet_counts: dict[str, int] = {}
        self._diagnostics_interval = diagnostics_interval_seconds
        self._summary_at = time.monotonic()
        self._disconnect_context = "remote_or_link_loss"

    def _matching_candidates(self, devices: list[Any]) -> list[Any]:
        def matches(device: Any) -> bool:
            name_matches = self.config.device_name is None or getattr(device, "name", None) == self.config.device_name
            metadata = getattr(device, "metadata", {}) or {}
            advertised_uuids = {str(item).lower() for item in metadata.get("uuids", [])}
            return name_matches and self.config.model_nbr_uuid in advertised_uuids

        return [device for device in devices if matches(device)]

    def select_strongest(self, devices: list[Any]) -> Any | None:
        """Select the strongest advertisement matching the configured Flowtime profile."""
        candidates = self._matching_candidates(devices)
        return max(candidates, key=lambda device: getattr(device, "rssi", None) if isinstance(getattr(device, "rssi", None), int | float) else float("-inf"), default=None)

    async def run(self) -> None:
        if not self.config.enabled:
            return
        from bleak import BleakClient, BleakScanner
        while not self._stopping:
            self._attempt += 1
            started = time.monotonic()
            stage = "scan"
            self._disconnected = asyncio.Event()
            self._connected_at = self._last_packet_at = None
            self._packet_counts = {}
            self._summary_at = started
            self._disconnect_context = "remote_or_link_loss"
            try:
                await self.status("connectionState", "connecting")
                LOG.info("Scanning for Flowtime headband: attempt=%s scanTimeoutSeconds=%s", self._attempt, self.config.scan_timeout_seconds)
                devices = await BleakScanner.discover(timeout=self.config.scan_timeout_seconds)
                candidate = self.select_strongest(devices)
                LOG.info("BLE scan completed: attempt=%s elapsedMs=%s discovered=%s matching=%s selectedRssi=%s", self._attempt, round((time.monotonic() - started) * 1000), len(devices), len(self._matching_candidates(devices)), getattr(candidate, "rssi", None))
                if candidate is None:
                    raise RuntimeError("No Flowtime headband matching the configured profile was found")
                stage = "connect"
                LOG.info("Connecting to Flowtime candidate: attempt=%s scanRssi=%s", self._attempt, getattr(candidate, "rssi", None))
                self._client = BleakClient(candidate, disconnected_callback=self._on_disconnected)
                await self._client.connect()
                stage = "subscribe"
                await self._subscribe()
                # Do not start device capture until the algorithm has a clean session.
                # This makes every post-FF21 packet eligible for automatic append.
                stage = "initialize_capture"
                await self.device_ready()
                stage = "start_capture"
                await self._client.write_gatt_char(FF21, b"\x05", response=True)
                # A connection is only published after all notifications, the FF21
                # start command, and the per-session algorithm initialization succeed.
                if not self._client.is_connected or self._disconnected.is_set():
                    raise RuntimeError("Headband disconnected before capture setup completed")
                self._connected_at = time.monotonic()
                LOG.info("BLE capture ready: attempt=%s setupElapsedMs=%s", self._attempt, round((self._connected_at - started) * 1000))
                stage = "capture"
                await self.status("connectionState", "connected")
                await self._read_battery()
                while self._client.is_connected and not self._stopping:
                    try:
                        await asyncio.wait_for(self._disconnected.wait(), timeout=1)
                        break
                    except TimeoutError:
                        self._log_packet_summary()
                await self._drain_callbacks()
                self._log_packet_summary(force=True)
                await self.status("connectionState", "disconnected")
            except Exception as exc:
                LOG.exception("Flowtime connection failed: attempt=%s stage=%s elapsedMs=%s errorType=%s retryDelaySeconds=%s", self._attempt, stage, round((time.monotonic() - started) * 1000), type(exc).__name__, self.config.reconnect_delay_seconds)
                if self.error:
                    await self.error(str(exc))
                await self._disconnect_after_failure()
                await self._drain_callbacks()
                await self.status("connectionState", "disconnected")
            if self._stopping:
                break
            LOG.info("BLE reconnect scheduled: attempt=%s delaySeconds=%s", self._attempt, self.config.reconnect_delay_seconds)
            await asyncio.sleep(self.config.reconnect_delay_seconds)

    def _on_disconnected(self, client: Any) -> None:
        # A callback from an old client must not tear down a newer capture.
        if client is not self._client or self._disconnected.is_set():
            return
        self._disconnected.set()
        current = time.monotonic()
        LOG.warning("BLE link disconnected: attempt=%s context=%s connectedDurationMs=%s lastPacketAgeMs=%s packetCounts=%s backendReason=unavailable", self._attempt, self._disconnect_context, round((current - self._connected_at) * 1000) if self._connected_at is not None else None, round((current - self._last_packet_at) * 1000) if self._last_packet_at is not None else None, self._packet_counts)

    def _schedule_callback(self, callback: Awaitable[None], stage: str) -> None:
        task = asyncio.create_task(self._run_callback(callback, stage))
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)

    async def _run_callback(self, callback: Awaitable[None], stage: str) -> None:
        try:
            await callback
        except Exception:
            LOG.exception("BLE callback processing failed: attempt=%s stage=%s; BLE link state unchanged", self._attempt, stage)

    async def _drain_callbacks(self) -> None:
        if self._callback_tasks:
            await asyncio.gather(*tuple(self._callback_tasks))

    def _log_packet_summary(self, *, force: bool = False) -> None:
        current = time.monotonic()
        if not force and (not self._diagnostics_interval or current - self._summary_at < self._diagnostics_interval):
            return
        LOG.info("BLE receive summary: attempt=%s connected=%s packetCounts=%s lastPacketAgeMs=%s", self._attempt, bool(self._client and self._client.is_connected), self._packet_counts, round((current - self._last_packet_at) * 1000) if self._last_packet_at is not None else None)
        self._summary_at = current

    async def _disconnect_after_failure(self) -> None:
        """Do not leave a device-side BLE link open after incomplete subscription."""
        if self._client and self._client.is_connected:
            try:
                self._disconnect_context = "setup_failure_cleanup"
                await self._client.disconnect()
            except Exception:
                LOG.exception("Failed to disconnect Flowtime after connection setup failure")
        self._client = None

    async def _subscribe(self) -> None:
        assert self._client
        client = self._client
        def schedule(characteristic: str, value: bytearray) -> None:
            if self._stopping or self._client is not client:
                return
            label = {FF31: "ff31", FF32: "ff32", FF51: "ff51", BATTERY: "battery"}[characteristic]
            self._last_packet_at = time.monotonic()
            self._packet_counts[label] = self._packet_counts.get(label, 0) + 1
            # Copy at callback time; backend bytearrays may be reused.
            self._schedule_callback(self._process_notification(characteristic, bytes(value)), label)
        for characteristic in REQUIRED_NOTIFICATION_CHARACTERISTICS:
            # Bleak notification callbacks are synchronous on all supported backends.
            # Schedule the async state update rather than relying on backend-specific
            # coroutine callback behavior.
            await self._client.start_notify(characteristic, lambda sender, value, c=characteristic: schedule(c, value))
        for characteristic in OPTIONAL_NOTIFICATION_CHARACTERISTICS:
            if not self._supports(characteristic):
                LOG.info("Optional Flowtime notification is not exposed by this headband; continuing without it")
                continue
            try:
                await self._client.start_notify(characteristic, lambda sender, value, c=characteristic: schedule(c, value))
            except Exception as exc:
                LOG.warning("Optional battery notification unavailable: errorType=%s; capture continues", type(exc).__name__)
        LOG.info("Subscribed to required Flowtime EEG, wear-state, and heart-rate notifications")

    async def _process_notification(self, characteristic: str, value: bytes) -> None:
        label = {FF31: "ff31", FF32: "ff32", FF51: "ff51", BATTERY: "battery"}[characteristic]
        await self.packet(label, value)
        if characteristic == FF32:
            state = wear_state_from_packet(value)
            if state == "unknown":
                LOG.warning("Invalid wear-state packet: packetBytes=%s expectedBytes=2", len(value))
            await self.status("wearState", state)
        elif characteristic == BATTERY:
            percent = battery_percent_from_packet(value)
            if percent is None:
                LOG.warning("Invalid battery packet: packetBytes=%s expectedBytes=1 range=0..100", len(value))
            await self.status("batteryPercent", percent)

    async def _read_battery(self) -> None:
        """Best-effort initial read after the recording session has started."""
        if not self._supports(BATTERY):
            return
        client = self._client
        try:
            value = bytes(await asyncio.wait_for(client.read_gatt_char(BATTERY), timeout=2))
        except Exception as exc:
            LOG.warning("Initial battery read unavailable: errorType=%s; capture continues", type(exc).__name__)
            return
        if self._client is client and client.is_connected and not self._disconnected.is_set() and not self._stopping:
            await self._run_callback(self._process_notification(BATTERY, value), "battery_read")

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
        self._disconnect_context = "gateway_stop"
        if self._client and self._client.is_connected:
            try:
                await self._client.write_gatt_char(FF21, b"\x06", response=True)
            except Exception:
                LOG.exception("Failed to send Flowtime stop command; attempting link disconnect")
            try:
                await self._client.disconnect()
            except Exception:
                LOG.exception("Failed to disconnect Flowtime during stop")
        await self._drain_callbacks()
        if self._client and self._client.is_connected:
            raise RuntimeError("Flowtime device link is still connected after stop")
