"""Serial headset discovery, framing, control responses, and packet-loss telemetry."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from glob import glob
import logging
from pathlib import Path
import re
import time
from typing import Any, Awaitable, Callable, Iterable

from ..config import SerialConfig
from ..device.packet import DevicePacket, wall_clock_ms

LOG = logging.getLogger(__name__)
HANDSHAKE = bytes.fromhex("AA 55 01 01 01 01 6F")
START_COMMAND = b"\xE1"
STOP_COMMAND = b"\xE0"
EXPECTED_HANDSHAKE_ACK_RESPONSE = b"\x01"
FRAME_HEADER = b"\xAA\xAA\xAA"
FRAME_TAIL = b"\xBB\xBB\xBB"
FRAME_BYTES = 28
EEG_START = 4
EEG_END = 24
HR_OFFSET = 24
SEQUENCE_MODULUS = 1 << 16
SEQUENCE_HALF_RANGE = 1 << 15
RECENT_SEQUENCE_WINDOW = 4096
LOG_TEXT_LIMIT = 512
DISCARDED_BYTES_LOG_INTERVAL = 4096


def _safe_log_text(value: object, limit: int = LOG_TEXT_LIMIT) -> str:
    """Bound exception text and remove control characters before logging."""

    return "".join(character if character.isprintable() else " " for character in str(value))[:limit]


def _serial_discovery_inventory(config: SerialConfig) -> dict[str, int | bool | str]:
    """Return node counts that explain why discovery found no candidate."""

    by_id = glob("/dev/serial/by-id/*")
    tty_acm = glob("/dev/ttyACM*")
    tty_usb = glob("/dev/ttyUSB*")
    return {
        "byIdEntries": len(by_id),
        "ttyACMEntries": len(tty_acm),
        "ttyUSBEntries": len(tty_usb),
        "configuredPathExists": (
            "not_applicable" if config.device == "auto" else Path(config.device).exists()
        ),
    }


def _longest_handshake_prefix(data: bytes | bytearray) -> int:
    """Measure wrong/partial handshakes without logging unknown serial bytes."""

    return max(
        (length for length in range(1, len(HANDSHAKE)) if HANDSHAKE[:length] in data),
        default=0,
    )


@dataclass(frozen=True)
class LossSnapshot:
    base_sequence: int | None
    highest_sequence: int | None
    expected_packets: int
    received_unique_packets: int
    lost_packets: int
    loss_rate_percent: float
    duplicate_packets: int
    out_of_order_packets: int
    late_packets: int


@dataclass(frozen=True)
class SequenceObservation:
    classification: str
    sequence: int
    expected_sequence: int | None
    gap_packets: int
    snapshot: LossSnapshot


class SequenceLossTracker:
    """RFC 3550-style cumulative loss using extended 16-bit sequence numbers.

    Expected packets are ``extended_highest - base + 1``. Received packets count
    unique packets only, so duplicates never improve the loss rate. Reordered
    packets within a bounded window can fill an earlier gap and reduce loss.
    """

    def __init__(self, recent_window: int = RECENT_SEQUENCE_WINDOW) -> None:
        self.recent_window = recent_window
        self.base_extended: int | None = None
        self.highest_extended: int | None = None
        self.received_unique = 0
        self.duplicates = 0
        self.out_of_order = 0
        self.late = 0
        self._recent_order: deque[int] = deque()
        self._recent_seen: set[int] = set()

    def _extend(self, sequence: int) -> int:
        assert self.highest_extended is not None
        cycle = self.highest_extended & ~(SEQUENCE_MODULUS - 1)
        candidate = cycle | sequence
        delta = candidate - self.highest_extended
        if delta < -SEQUENCE_HALF_RANGE:
            candidate += SEQUENCE_MODULUS
        elif delta > SEQUENCE_HALF_RANGE:
            candidate -= SEQUENCE_MODULUS
        return candidate

    def _remember(self, extended: int) -> None:
        self._recent_seen.add(extended)
        self._recent_order.append(extended)
        while len(self._recent_order) > self.recent_window:
            expired = self._recent_order.popleft()
            self._recent_seen.discard(expired)

    def observe(self, sequence: int) -> SequenceObservation:
        if not 0 <= sequence < SEQUENCE_MODULUS:
            raise ValueError("sequence must be an unsigned 16-bit value")
        if self.highest_extended is None:
            self.base_extended = sequence
            self.highest_extended = sequence
            self.received_unique = 1
            self._remember(sequence)
            return SequenceObservation("baseline", sequence, None, 0, self.snapshot())

        expected_sequence = (self.highest_extended + 1) % SEQUENCE_MODULUS
        extended = self._extend(sequence)
        gap_packets = 0
        if extended > self.highest_extended:
            gap_packets = extended - self.highest_extended - 1
            classification = "gap" if gap_packets else "in_order"
            self.highest_extended = extended
            self.received_unique += 1
            self._remember(extended)
        elif extended in self._recent_seen:
            classification = "duplicate"
            self.duplicates += 1
        elif extended >= max(self.base_extended or 0, self.highest_extended - self.recent_window + 1):
            classification = "out_of_order"
            self.out_of_order += 1
            self.received_unique += 1
            self._remember(extended)
        else:
            classification = "late"
            self.late += 1
        return SequenceObservation(
            classification,
            sequence,
            expected_sequence,
            gap_packets,
            self.snapshot(),
        )

    def snapshot(self) -> LossSnapshot:
        if self.base_extended is None or self.highest_extended is None:
            return LossSnapshot(None, None, 0, 0, 0, 0.0, self.duplicates, self.out_of_order, self.late)
        expected = self.highest_extended - self.base_extended + 1
        lost = max(0, expected - self.received_unique)
        return LossSnapshot(
            self.base_extended % SEQUENCE_MODULUS,
            self.highest_extended % SEQUENCE_MODULUS,
            expected,
            self.received_unique,
            lost,
            lost * 100.0 / expected if expected else 0.0,
            self.duplicates,
            self.out_of_order,
            self.late,
        )


def discover_serial_candidates(config: SerialConfig) -> list[str]:
    """Return stable, de-duplicated USB TTY candidates in deterministic order."""

    if config.device != "auto":
        return [config.device] if _resolved_usb_tty(config.device, config.candidate_types) else []
    paths = sorted(glob("/dev/serial/by-id/*"))
    for candidate_type in config.candidate_types:
        paths.extend(sorted(glob(f"/dev/{candidate_type}*")))
    candidates: list[str] = []
    resolved_seen: set[str] = set()
    for value in paths:
        resolved = _resolved_usb_tty(value, config.candidate_types)
        if resolved is None:
            continue
        if resolved not in resolved_seen:
            candidates.append(value)
            resolved_seen.add(resolved)
    # Prefer persistent by-id aliases. Within each group, use the USB parent,
    # physical sysfs path, and interface number so multi-interface devices keep
    # the same probe order even if ttyACM/ttyUSB kernel indices change.
    return sorted(
        candidates,
        key=lambda candidate: _serial_candidate_order_key(candidate, serial_candidate_metadata(candidate)),
    )


def _resolved_usb_tty(path: str, candidate_types: tuple[str, ...]) -> str | None:
    """Resolve a configured node only when it is a USB-derived ttyACM/ttyUSB device."""

    try:
        resolved = Path(path).resolve(strict=True)
    except OSError:
        return None
    allowed_names = "|".join(re.escape(candidate_type) for candidate_type in candidate_types)
    if re.fullmatch(rf"(?:{allowed_names})[0-9]+", resolved.name) is None:
        return None
    if not resolved.is_char_device():
        return None
    if serial_candidate_metadata(path)["usbParent"] is None:
        return None
    return str(resolved)


def _serial_candidate_order_key(candidate: str, metadata: dict[str, str | None]) -> tuple[int, str, int, str, str]:
    """Build the deterministic by-id/USB-parent/interface probe order."""

    interface = metadata["interface"]
    try:
        interface_number = int(interface or "", 16)
    except ValueError:
        interface_number = 1 << 30
    return (
        0 if Path(candidate).parent == Path("/dev/serial/by-id") else 1,
        metadata["usbParent"] or "",
        interface_number,
        metadata["physicalPath"] or "",
        candidate,
    )


def serial_candidate_metadata(path: str) -> dict[str, str | None]:
    """Read best-effort Linux sysfs identity fields for field diagnostics."""

    try:
        resolved_path = str(Path(path).resolve(strict=True))
    except OSError:
        resolved_path = str(Path(path))
    metadata: dict[str, str | None] = {
        "resolvedPath": resolved_path,
        "vid": None,
        "pid": None,
        "usbSerial": None,
        "interface": None,
        "driver": None,
        "usbParent": None,
        "physicalPath": None,
    }
    sysfs_device = Path("/sys/class/tty") / Path(resolved_path).name / "device"
    try:
        current = sysfs_device.resolve(strict=True)
    except OSError:
        return metadata
    metadata["physicalPath"] = str(current)
    for node in (current, *current.parents):
        if metadata["driver"] is None:
            try:
                metadata["driver"] = (node / "driver").resolve(strict=True).name
            except OSError:
                pass
        for key, filename in (
            ("vid", "idVendor"),
            ("pid", "idProduct"),
            ("usbSerial", "serial"),
            ("interface", "bInterfaceNumber"),
        ):
            if metadata[key] is not None:
                continue
            try:
                value = (node / filename).read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if value:
                metadata[key] = _safe_log_text(value, 128)
        if metadata["usbParent"] is None and ((node / "idVendor").is_file() or (node / "idProduct").is_file()):
            metadata["usbParent"] = _safe_log_text(node, 512)
        if node == Path("/sys"):
            break
    return metadata


def _valid_frame_offset(buffer: bytes | bytearray) -> int | None:
    """Return the first complete structurally valid frame without consuming it."""

    search_from = 0
    while True:
        offset = buffer.find(FRAME_HEADER, search_from)
        if offset < 0:
            return None
        if len(buffer) - offset < FRAME_BYTES:
            return None
        if (
            buffer[offset + 3] == FRAME_BYTES
            and buffer[offset + FRAME_BYTES - 3 : offset + FRAME_BYTES] == FRAME_TAIL
        ):
            return offset
        search_from = offset + 1


def _open_serial(path: str, config: SerialConfig) -> Any:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("pyserial is required for data_source.type=serial") from exc
    client = serial.Serial(
        port=None,
        baudrate=config.baud_rate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.1,
        write_timeout=config.command_response_timeout_ms / 1000,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
        exclusive=True,
    )
    # Set inactive modem-control levels before open. Passing a live port to the
    # constructor can briefly assert the library defaults and reset some USB
    # serial devices before these values are applied.
    client.dtr = config.dtr
    client.rts = config.rts
    client.port = path
    try:
        client.open()
    except Exception:
        client.close()
        raise
    return client


class SerialAdapter:
    """Discover the first validated device and expose BLE-compatible raw packets."""

    def __init__(
        self,
        config: SerialConfig,
        packet: Callable[[DevicePacket], Awaitable[None]],
        status: Callable[[str, object], Awaitable[None]],
        device_ready: Callable[[], Awaitable[bool]],
        error: Callable[[str], Awaitable[None]] | None = None,
        *,
        candidate_provider: Callable[[SerialConfig], Iterable[str]] = discover_serial_candidates,
        serial_factory: Callable[[str, SerialConfig], Any] = _open_serial,
        identity_provider: Callable[[str], dict[str, str | None]] = serial_candidate_metadata,
    ) -> None:
        self.config = config
        self.packet = packet
        self.status = status
        self.device_ready = device_ready
        self.error = error
        self.candidate_provider = candidate_provider
        self.serial_factory = serial_factory
        self.identity_provider = identity_provider
        self._client: Any | None = None
        self._target: str | None = None
        self._stopping = False
        self._capture_started = False
        self._io_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._loss = SequenceLossTracker()
        self._last_summary_snapshot = self._loss.snapshot()
        self._stats: dict[str, int | float] = {}
        self._reset_stats()

    def _reset_stats(self) -> None:
        self._loss = SequenceLossTracker()
        self._last_summary_snapshot = self._loss.snapshot()
        self._stats = {
            "frames": 0,
            "frameBytes": 0,
            "readBytes": 0,
            "invalidFrames": 0,
            "discardedBytes": 0,
            "bufferOverflows": 0,
            "controlResponses": 0,
            "controlResponseBytes": 0,
            "controlTimeouts": 0,
            "controlUnexpectedResponses": 0,
            "handshakeAckWrites": 0,
            "handshakeAckWriteBytes": 0,
            "handshakeAckRepeatedFrames": 0,
            "handshakeAckPartialTimeouts": 0,
            "streamHandshakeFrames": 0,
            "startedAtMonotonic": time.monotonic(),
            "lastSummaryAtMonotonic": 0.0,
        }

    async def run(self) -> None:
        attempt = 0
        while not self._stopping:
            attempt += 1
            phase = "discover"
            target_selected = False
            existing_stream = b""
            existing_stream_received_at_ms: int | None = None
            try:
                self._reset_stats()
                await self.status("connectionState", "connecting")
                candidates = list(self.candidate_provider(self.config))
                inventory = _serial_discovery_inventory(self.config)
                LOG.info(
                    "Serial discovery started: attempt=%s candidates=%s deviceMode=%s captureProbeTimeoutMs=%s "
                    "byIdEntries=%s ttyACMEntries=%s ttyUSBEntries=%s configuredPathExists=%s",
                    attempt,
                    len(candidates),
                    self.config.device,
                    self.config.handshake_timeout_ms,
                    inventory["byIdEntries"],
                    inventory["ttyACMEntries"],
                    inventory["ttyUSBEntries"],
                    inventory["configuredPathExists"],
                )
                candidate_identities: dict[str, dict[str, str | None]] = {}
                for index, path in enumerate(candidates, start=1):
                    identity = self.identity_provider(path)
                    candidate_identities[path] = identity
                    LOG.info(
                        "Serial candidate discovered: attempt=%s candidateIndex=%s candidateCount=%s path=%s "
                        "resolvedPath=%s vid=%s pid=%s usbSerial=%s usbParent=%s interface=%s driver=%s physicalPath=%s",
                        attempt,
                        index,
                        len(candidates),
                        _safe_log_text(path),
                        _safe_log_text(identity["resolvedPath"]),
                        identity["vid"],
                        identity["pid"],
                        identity["usbSerial"],
                        _safe_log_text(identity["usbParent"]),
                        identity["interface"],
                        identity["driver"],
                        _safe_log_text(identity["physicalPath"]),
                    )
                if not candidates:
                    LOG.warning(
                        "Serial discovery found no usable candidates: attempt=%s deviceMode=%s candidateTypes=%s "
                        "byIdEntries=%s ttyACMEntries=%s ttyUSBEntries=%s configuredPathExists=%s "
                        "nextRetrySeconds=%s",
                        attempt,
                        self.config.device,
                        ",".join(self.config.candidate_types),
                        inventory["byIdEntries"],
                        inventory["ttyACMEntries"],
                        inventory["ttyUSBEntries"],
                        inventory["configuredPathExists"],
                        self.config.reconnect_delay_seconds,
                    )
                    raise ConnectionError("No USB-derived serial candidates were found")
                phase = "candidate_probe"
                opened_candidate_count = 0
                rejected_candidate_count = 0
                probe_failures: list[tuple[str, Exception]] = []
                for index, path in enumerate(candidates, start=1):
                    if self._stopping:
                        return
                    client = None
                    try:
                        phase = "candidate_open"
                        client = await asyncio.to_thread(self.serial_factory, path, self.config)
                        opened_candidate_count += 1
                        LOG.info(
                            "Serial candidate opened: attempt=%s candidateIndex=%s candidateCount=%s path=%s",
                            attempt,
                            index,
                            len(candidates),
                            _safe_log_text(path),
                        )
                        identity = candidate_identities[path]
                        phase = "existing_stream_observation"
                        observed_stream = await self._observe_existing_stream(client, path)
                        if observed_stream is not None:
                            self._client, self._target = client, path
                            target_selected = True
                            existing_stream, existing_stream_received_at_ms = observed_stream
                            self._capture_started = True
                            LOG.info(
                                "Serial target selected: path=%s resolvedPath=%s vid=%s pid=%s usbSerial=%s "
                                "usbParent=%s interface=%s driver=%s physicalPath=%s selectionMode=%s matchBasis=%s",
                                _safe_log_text(path),
                                _safe_log_text(identity["resolvedPath"]),
                                identity["vid"],
                                identity["pid"],
                                identity["usbSerial"],
                                _safe_log_text(identity["usbParent"]),
                                identity["interface"],
                                identity["driver"],
                                _safe_log_text(identity["physicalPath"]),
                                "existing_valid_frame",
                                "valid_28_byte_frame",
                            )
                            await self.status("connectionState", "validated")
                            break
                        phase = "handshake_ack_probe"
                        LOG.info(
                            "Serial active handshake ACK probe started: attempt=%s candidateIndex=%s "
                            "candidateCount=%s path=%s ackBytes=%s expectedResponse=single_byte_0x01",
                            attempt,
                            index,
                            len(candidates),
                            _safe_log_text(path),
                            len(HANDSHAKE),
                        )
                        response = await self._send_handshake_ack(client)
                        if response:
                            self._client, self._target = client, path
                            target_selected = True
                            LOG.info(
                                "Serial target selected: path=%s resolvedPath=%s vid=%s pid=%s usbSerial=%s "
                                "usbParent=%s interface=%s driver=%s physicalPath=%s selectionMode=%s matchBasis=%s",
                                _safe_log_text(path),
                                _safe_log_text(identity["resolvedPath"]),
                                identity["vid"],
                                identity["pid"],
                                identity["usbSerial"],
                                _safe_log_text(identity["usbParent"]),
                                identity["interface"],
                                identity["driver"],
                                _safe_log_text(identity["physicalPath"]),
                                "active_ack_probe",
                                "standalone_0x01",
                            )
                            await self.status("connectionState", "validated")
                            break
                        rejected_candidate_count += 1
                    except Exception as exc:
                        probe_failures.append((path, exc))
                        LOG.warning(
                            "Serial candidate probe failed: attempt=%s candidateIndex=%s candidateCount=%s "
                            "path=%s errorType=%s reason=%s",
                            attempt,
                            index,
                            len(candidates),
                            _safe_log_text(path),
                            type(exc).__name__,
                            _safe_log_text(exc),
                        )
                    finally:
                        if client is not None and client is not self._client:
                            await self._close_client(client)
                if self._client is None:
                    if rejected_candidate_count:
                        await self.status("connectionState", "validation_failed")
                        raise TimeoutError(
                            "Serial candidates opened but none returned standalone 0x01 after active ACK"
                        )
                    if probe_failures:
                        failed_path, probe_error = probe_failures[-1]
                        action = "open" if opened_candidate_count == 0 else "probe"
                        raise ConnectionError(
                            f"Unable to {action} any usable serial candidate; "
                            f"lastPath={_safe_log_text(failed_path)} "
                            f"lastErrorType={type(probe_error).__name__} "
                            f"lastReason={_safe_log_text(probe_error)}"
                        ) from probe_error
                    raise TimeoutError("No serial candidate passed the active handshake ACK probe")

                phase = "algorithm_initialize"
                LOG.info(
                    "Serial local algorithm preparation started: attempt=%s target=%s startCommandSent=false",
                    attempt,
                    _safe_log_text(self._target),
                )
                algorithm_ready = await self.device_ready()
                if not algorithm_ready:
                    LOG.error(
                        "Serial algorithm preparation failed after device validation: attempt=%s target=%s "
                        "reason=local_algorithm_not_ready startCommandSent=false",
                        attempt,
                        _safe_log_text(self._target),
                    )
                    raise ConnectionError("Local algorithm is not ready; serial start command was not sent")
                if existing_stream:
                    LOG.info(
                        "Serial existing capture adopted: attempt=%s target=%s commandSent=false "
                        "observedValidFrame=true connectionState=validated",
                        attempt,
                        _safe_log_text(self._target),
                    )
                    phase = "streaming"
                    await self._stream(existing_stream, existing_stream_received_at_ms)
                    if not self._stopping:
                        raise ConnectionError("Serial stream ended")
                    continue
                phase = "start_command_write"
                LOG.info(
                    "Serial capture enable started: attempt=%s target=%s algorithmReady=true "
                    "command=E1 responseExpected=false",
                    attempt,
                    _safe_log_text(self._target),
                )
                try:
                    await self._send_command(START_COMMAND, "start")
                except Exception:
                    LOG.exception(
                        "Serial capture enable failed after device validation: attempt=%s target=%s command=E1 "
                        "reason=control_write_error responseExpected=false",
                        attempt,
                        _safe_log_text(self._target),
                    )
                    raise
                self._capture_started = True
                LOG.info(
                    "Serial capture enabled: attempt=%s target=%s command=E1 "
                    "responseExpected=false connectionState=validated deviceValidationMode=ack_01",
                    attempt,
                    _safe_log_text(self._target),
                )
                phase = "streaming"
                await self._stream(b"")
                if not self._stopping:
                    raise ConnectionError("Serial stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_failure = LOG.warning if isinstance(exc, (ConnectionError, TimeoutError, OSError)) else LOG.exception
                log_failure(
                    "Serial connection failed: attempt=%s phase=%s target=%s errorType=%s reason=%s",
                    attempt,
                    phase,
                    _safe_log_text(self._target),
                    type(exc).__name__,
                    _safe_log_text(exc),
                )
                if self.error:
                    await self.error(_safe_log_text(exc))
            finally:
                if self._client is not None:
                    if self._capture_started:
                        await self._send_stop_best_effort("adapter_cleanup")
                    self._log_stats("disconnect")
                    await self._close_client(self._client)
                self._client = None
                self._target = None
                self._capture_started = False
                await self.status(
                    "connectionState",
                    "disconnected" if target_selected else "not_connected",
                )
            if not self._stopping:
                LOG.info(
                    "Serial reconnect scheduled: attempt=%s nextAttempt=%s delaySeconds=%s",
                    attempt,
                    attempt + 1,
                    self.config.reconnect_delay_seconds,
                )
                await asyncio.sleep(self.config.reconnect_delay_seconds)

    async def _observe_existing_stream(self, client: Any, path: str) -> tuple[bytes, int] | None:
        """Return bytes and their read-boundary time for an existing valid stream."""

        buffer = bytearray()
        started = time.monotonic()
        deadline = started + self.config.handshake_timeout_ms / 1000
        previous_timeout = getattr(client, "timeout", None)
        try:
            while not self._stopping and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                client.timeout = min(0.1, max(0.001, remaining))
                async with self._io_lock:
                    chunk = bytes(await asyncio.to_thread(client.read, 4096))
                if not chunk:
                    await asyncio.sleep(min(0.01, max(0.0, remaining)))
                    continue
                received_at_ms = wall_clock_ms()
                buffer.extend(chunk)
                if len(buffer) > self.config.max_buffer_bytes:
                    del buffer[: len(buffer) - self.config.max_buffer_bytes]
                if _valid_frame_offset(buffer) is not None:
                    LOG.info(
                        "Serial existing capture detected: path=%s durationMs=%s bufferedBytes=%s "
                        "validFrameBytes=%s nextState=validated payloadLogged=false",
                        _safe_log_text(path),
                        int((time.monotonic() - started) * 1000),
                        len(buffer),
                        FRAME_BYTES,
                    )
                    return bytes(buffer), received_at_ms
        finally:
            client.timeout = previous_timeout
        LOG.info(
            "Serial existing capture not detected: path=%s durationMs=%s bufferedBytes=%s "
            "nextAction=active_ack_probe payloadLogged=false",
            _safe_log_text(path),
            int((time.monotonic() - started) * 1000),
            len(buffer),
        )
        return None

    async def _send_handshake_ack(self, client: Any | None = None) -> bytes:
        client = client or self._client
        if client is None:
            return b""
        started = time.monotonic()
        timeout_seconds = self.config.command_response_timeout_ms / 1000
        deadline = started + timeout_seconds
        response_buffer = bytearray()
        total_read_bytes = 0
        unexpected_bytes = 0
        longest_handshake_prefix = 0
        repeated_handshake_frames = 0
        ack_write_count = 0
        ack_write_bytes = 0
        response = b""

        async def write_ack() -> None:
            nonlocal ack_write_count, ack_write_bytes
            written = await asyncio.to_thread(client.write, HANDSHAKE)
            if written != len(HANDSHAKE):
                raise OSError(
                    f"Serial handshake ACK write was incomplete: expected={len(HANDSHAKE)} "
                    f"actual={written}"
                )
            await self._flush(client)
            ack_write_count += 1
            ack_write_bytes += written
            self._stats["handshakeAckWrites"] = int(self._stats["handshakeAckWrites"]) + 1
            self._stats["handshakeAckWriteBytes"] = (
                int(self._stats["handshakeAckWriteBytes"]) + written
            )

        async with self._io_lock:
            if hasattr(client, "reset_input_buffer"):
                await asyncio.to_thread(client.reset_input_buffer)
            await write_ack()
            LOG.info(
                "Serial handshake ACK written: command=handshake_ack ackWriteCount=%s "
                "ackWriteBytes=%s repeatedHandshakeFrames=%s writeSuccess=true payloadLogged=false",
                ack_write_count,
                ack_write_bytes,
                repeated_handshake_frames,
            )
            previous_timeout = getattr(client, "timeout", None)
            try:
                # Read one byte at a time so only the device's standalone
                # 0x01 acknowledgement is accepted. The headset does not
                # initiate this exchange; the write above starts it.
                while not self._stopping:
                    remaining_seconds = deadline - time.monotonic()
                    if remaining_seconds <= 0:
                        break
                    client.timeout = remaining_seconds
                    chunk = bytes(await asyncio.to_thread(client.read, 1))
                    if not chunk:
                        # A real pyserial timeout normally consumes the whole
                        # remaining interval. Keep the deadline authoritative
                        # if a driver returns early or a test double is
                        # non-blocking, without creating a busy loop.
                        await asyncio.sleep(min(0.01, max(0.0, remaining_seconds)))
                        continue
                    total_read_bytes += len(chunk)
                    response_buffer.extend(chunk)
                    longest_handshake_prefix = max(
                        longest_handshake_prefix,
                        _longest_handshake_prefix(response_buffer),
                    )

                    if response_buffer == EXPECTED_HANDSHAKE_ACK_RESPONSE:
                        response = EXPECTED_HANDSHAKE_ACK_RESPONSE
                        response_buffer.clear()
                        break

                    if HANDSHAKE.startswith(response_buffer):
                        if response_buffer == HANDSHAKE:
                            repeated_handshake_frames += 1
                            self._stats["handshakeAckRepeatedFrames"] = (
                                int(self._stats["handshakeAckRepeatedFrames"]) + 1
                            )
                            response_buffer.clear()
                            await write_ack()
                            LOG.warning(
                                "Serial repeated handshake received while awaiting ACK result: "
                                "command=handshake_ack repeatedHandshakeFrames=%s ackWriteCount=%s "
                                "ackWriteBytes=%s elapsedMs=%s action=ack_resent "
                                "payloadLogged=false",
                                repeated_handshake_frames,
                                ack_write_count,
                                ack_write_bytes,
                                int((time.monotonic() - started) * 1000),
                            )
                        continue

                    # Do not reinterpret an 0x01 embedded in a malformed or
                    # unknown response as the standalone ACK result.
                    unexpected_bytes += len(response_buffer)
                    response_buffer.clear()
            finally:
                client.timeout = previous_timeout
        duration_ms = int((time.monotonic() - started) * 1000)
        self._stats["controlResponseBytes"] = (
            int(self._stats["controlResponseBytes"]) + total_read_bytes
        )
        if total_read_bytes:
            self._stats["controlResponses"] = int(self._stats["controlResponses"]) + 1
        if response:
            LOG.info(
                "Serial handshake ACK response received: command=handshake_ack responseBytes=1 "
                "totalReadBytes=%s durationMs=%s responseClassification=single_byte_0x01 "
                "expectedAck01=true singleByteHex=01 repeatedHandshakeFrames=%s "
                "ackWriteCount=%s ackWriteBytes=%s unexpectedBytes=%s "
                "longestHandshakePrefixBytes=%s success=true acceptedByCurrentPolicy=true "
                "payloadLogged=false",
                total_read_bytes,
                duration_ms,
                repeated_handshake_frames,
                ack_write_count,
                ack_write_bytes,
                unexpected_bytes,
                longest_handshake_prefix,
            )
        else:
            self._stats["controlTimeouts"] = int(self._stats["controlTimeouts"]) + 1
            if unexpected_bytes or response_buffer:
                self._stats["controlUnexpectedResponses"] = (
                    int(self._stats["controlUnexpectedResponses"]) + 1
                )
            if response_buffer:
                self._stats["handshakeAckPartialTimeouts"] = (
                    int(self._stats["handshakeAckPartialTimeouts"]) + 1
                )
            LOG.warning(
                "Serial handshake ACK response timed out: command=handshake_ack timeoutMs=%s "
                "durationMs=%s totalReadBytes=%s repeatedHandshakeFrames=%s ackWriteCount=%s "
                "ackWriteBytes=%s unexpectedBytes=%s partialHandshakeBytes=%s "
                "longestHandshakePrefixBytes=%s expectedAck01=true success=false "
                "payloadLogged=false",
                self.config.command_response_timeout_ms,
                duration_ms,
                total_read_bytes,
                repeated_handshake_frames,
                ack_write_count,
                ack_write_bytes,
                unexpected_bytes,
                len(response_buffer),
                longest_handshake_prefix,
            )
        return response

    async def _send_command(self, command: bytes, name: str) -> None:
        """Write a protocol command which intentionally has no response."""

        if self._client is None:
            raise ConnectionError(f"Serial command cannot be sent without a client: {name}")
        started = time.monotonic()
        async with self._io_lock:
            written = await asyncio.to_thread(self._client.write, command)
            if written != len(command):
                raise OSError(
                    f"Serial command write was incomplete: command={name} "
                    f"expected={len(command)} actual={written}"
                )
            await self._flush(self._client)
        LOG.info(
            "Serial command sent: command=%s commandBytes=%s durationMs=%s "
            "responseExpected=false success=true",
            name,
            len(command),
            int((time.monotonic() - started) * 1000),
        )

    async def _send_stop_best_effort(self, reason: str) -> None:
        async with self._stop_lock:
            if not self._capture_started:
                return
            # Claim the single stop attempt before awaiting I/O so concurrent
            # service-stop and adapter-cleanup paths cannot both send 0xE0.
            self._capture_started = False
            try:
                await self._send_command(STOP_COMMAND, "stop")
                LOG.info(
                    "Serial stop command completed: reason=%s responseExpected=false success=true",
                    reason,
                )
            except Exception:
                LOG.exception("Serial stop command failed: reason=%s", reason)

    async def _stream(self, initial: bytes, initial_received_at_ms: int | None = None) -> None:
        buffer = bytearray(initial)
        buffer_received_at_ms = initial_received_at_ms
        last_frame_at = time.monotonic()
        self._stats["readBytes"] = int(self._stats["readBytes"]) + len(initial)
        while not self._stopping:
            parsed = await self._consume_frames(buffer, buffer_received_at_ms)
            if parsed:
                last_frame_at = time.monotonic()
            now = time.monotonic()
            if now - float(self._stats["lastSummaryAtMonotonic"]) >= self.config.stats_interval_seconds:
                self._log_stats("periodic")
                self._stats["lastSummaryAtMonotonic"] = now
            if now - last_frame_at >= self.config.data_timeout_seconds:
                LOG.warning(
                    "Serial valid-frame timeout: target=%s timeoutSeconds=%.3f readBytes=%s frames=%s "
                    "invalidFrames=%s discardedBytes=%s bufferOverflows=%s bufferedBytes=%s "
                    "streamHandshakeFrames=%s",
                    _safe_log_text(self._target),
                    self.config.data_timeout_seconds,
                    self._stats["readBytes"],
                    self._stats["frames"],
                    self._stats["invalidFrames"],
                    self._stats["discardedBytes"],
                    self._stats["bufferOverflows"],
                    len(buffer),
                    self._stats["streamHandshakeFrames"],
                )
                raise TimeoutError(f"No valid serial data frame for {self.config.data_timeout_seconds:.3f} seconds")
            client = self._client
            if client is None:
                return
            async with self._io_lock:
                chunk = bytes(await asyncio.to_thread(client.read, 4096))
            if chunk:
                buffer_received_at_ms = wall_clock_ms()
                self._stats["readBytes"] = int(self._stats["readBytes"]) + len(chunk)
                buffer.extend(chunk)
                if len(buffer) > self.config.max_buffer_bytes:
                    discarded = len(buffer) - self.config.max_buffer_bytes
                    del buffer[:discarded]
                    self._stats["discardedBytes"] = int(self._stats["discardedBytes"]) + discarded
                    self._stats["bufferOverflows"] = int(self._stats["bufferOverflows"]) + 1
                    LOG.warning(
                        "Serial receive buffer truncated: discardedBytes=%s bufferOverflows=%s maxBufferBytes=%s",
                        discarded,
                        self._stats["bufferOverflows"],
                        self.config.max_buffer_bytes,
                    )

    async def _consume_frames(self, buffer: bytearray, received_at_ms: int | None = None) -> int:
        parsed = 0
        while True:
            offset = buffer.find(FRAME_HEADER)
            if offset < 0:
                self._record_stream_handshakes(buffer, len(buffer))
                keep = min(len(buffer), len(FRAME_HEADER) - 1)
                discarded = len(buffer) - keep
                if discarded:
                    del buffer[:discarded]
                    self._record_discarded_bytes(discarded, "frame_header_not_found", len(buffer))
                return parsed
            if offset:
                self._record_stream_handshakes(buffer[:offset], len(buffer))
                del buffer[:offset]
                self._record_discarded_bytes(offset, "bytes_before_frame_header", len(buffer))
            if len(buffer) < 4:
                return parsed
            if buffer[3] != FRAME_BYTES:
                observed_length = buffer[3]
                del buffer[0]
                self._stats["invalidFrames"] = int(self._stats["invalidFrames"]) + 1
                self._log_invalid_frame("length", len(buffer), observed_length)
                continue
            if len(buffer) < FRAME_BYTES:
                return parsed
            frame = bytes(buffer[:FRAME_BYTES])
            if frame[-len(FRAME_TAIL):] != FRAME_TAIL:
                del buffer[0]
                self._stats["invalidFrames"] = int(self._stats["invalidFrames"]) + 1
                self._log_invalid_frame("tail", len(buffer), FRAME_BYTES)
                continue
            del buffer[:FRAME_BYTES]
            sequence = int.from_bytes(frame[4:6], "big", signed=False)
            observation = self._loss.observe(sequence)
            self._stats["frames"] = int(self._stats["frames"]) + 1
            self._stats["frameBytes"] = int(self._stats["frameBytes"]) + len(frame)
            parsed += 1
            if observation.classification == "gap":
                LOG.warning(
                    "Serial packet loss detected: expectedSequence=%s actualSequence=%s gapPackets=%s "
                    "expectedPackets=%s receivedUniquePackets=%s lostPackets=%s lossRatePercent=%.6f",
                    observation.expected_sequence,
                    observation.sequence,
                    observation.gap_packets,
                    observation.snapshot.expected_packets,
                    observation.snapshot.received_unique_packets,
                    observation.snapshot.lost_packets,
                    observation.snapshot.loss_rate_percent,
                )
            elif observation.classification in {"duplicate", "out_of_order", "late"}:
                LOG.warning(
                    "Serial sequence anomaly: classification=%s expectedSequence=%s actualSequence=%s "
                    "duplicates=%s outOfOrder=%s late=%s lostPackets=%s lossRatePercent=%.6f",
                    observation.classification,
                    observation.expected_sequence,
                    observation.sequence,
                    observation.snapshot.duplicate_packets,
                    observation.snapshot.out_of_order_packets,
                    observation.snapshot.late_packets,
                    observation.snapshot.lost_packets,
                    observation.snapshot.loss_rate_percent,
                )
            frame_received_at_ms = received_at_ms if received_at_ms is not None else wall_clock_ms()
            # Preserve the confirmed 28-byte transport frame independently of
            # the compatibility projections consumed by the existing algorithm.
            await self.packet(DevicePacket("serial", "serial.frame", frame, frame_received_at_ms))
            await self.packet(DevicePacket("serial", "ff31", frame[EEG_START:EEG_END], frame_received_at_ms))
            await self.packet(DevicePacket("serial", "ff51", frame[HR_OFFSET:HR_OFFSET + 1], frame_received_at_ms))

    def _record_stream_handshakes(self, discarded_region: bytes | bytearray, buffered_bytes: int) -> None:
        """Count fixed handshakes only outside confirmed data frames."""

        handshake_frames = discarded_region.count(HANDSHAKE)
        if not handshake_frames:
            return
        previous = int(self._stats["streamHandshakeFrames"])
        total = previous + handshake_frames
        self._stats["streamHandshakeFrames"] = total
        if total <= 3 or total & (total - 1) == 0:
            LOG.warning(
                "Serial fixed handshake observed during data stream: observedNow=%s "
                "streamHandshakeFrames=%s bufferedBytes=%s payloadLogged=false",
                handshake_frames,
                total,
                buffered_bytes,
            )

    def _record_discarded_bytes(self, discarded: int, reason: str, buffered_bytes: int) -> None:
        previous = int(self._stats["discardedBytes"])
        total = previous + discarded
        self._stats["discardedBytes"] = total
        if previous == 0 or total // DISCARDED_BYTES_LOG_INTERVAL > previous // DISCARDED_BYTES_LOG_INTERVAL:
            LOG.warning(
                "Serial bytes discarded while resynchronizing: reason=%s discardedNow=%s discardedBytes=%s "
                "bufferedBytes=%s payloadLogged=false",
                reason,
                discarded,
                total,
                buffered_bytes,
            )

    def _log_invalid_frame(self, reason: str, buffered_bytes: int, observed_length: int) -> None:
        count = int(self._stats["invalidFrames"])
        if count <= 3 or count & (count - 1) == 0:
            LOG.warning(
                "Serial invalid frame rejected: reason=%s invalidFrames=%s observedLength=%s "
                "expectedLength=%s bufferedBytes=%s payloadLogged=false",
                reason,
                count,
                observed_length,
                FRAME_BYTES,
                buffered_bytes,
            )

    def _log_stats(self, reason: str) -> None:
        snapshot = self._loss.snapshot()
        previous = self._last_summary_snapshot
        interval_expected = max(0, snapshot.expected_packets - previous.expected_packets)
        interval_received = max(0, snapshot.received_unique_packets - previous.received_unique_packets)
        interval_lost = max(0, interval_expected - interval_received)
        interval_loss_rate = interval_lost * 100.0 / interval_expected if interval_expected else 0.0
        LOG.info(
            "Serial capture summary: reason=%s target=%s uptimeSeconds=%.3f frames=%s frameBytes=%s "
            "readBytes=%s invalidFrames=%s discardedBytes=%s bufferOverflows=%s baseSequence=%s "
            "highestSequence=%s expectedPackets=%s receivedUniquePackets=%s lostPackets=%s "
            "lossRatePercent=%.6f intervalExpectedPackets=%s intervalReceivedUniquePackets=%s "
            "intervalLostPackets=%s intervalLossRatePercent=%.6f duplicates=%s outOfOrder=%s late=%s "
            "controlResponses=%s controlResponseBytes=%s controlTimeouts=%s "
            "controlUnexpectedResponses=%s handshakeAckWrites=%s handshakeAckWriteBytes=%s "
            "handshakeAckRepeatedFrames=%s handshakeAckPartialTimeouts=%s "
            "streamHandshakeFrames=%s",
            reason,
            _safe_log_text(self._target),
            time.monotonic() - float(self._stats["startedAtMonotonic"]),
            self._stats["frames"],
            self._stats["frameBytes"],
            self._stats["readBytes"],
            self._stats["invalidFrames"],
            self._stats["discardedBytes"],
            self._stats["bufferOverflows"],
            snapshot.base_sequence,
            snapshot.highest_sequence,
            snapshot.expected_packets,
            snapshot.received_unique_packets,
            snapshot.lost_packets,
            snapshot.loss_rate_percent,
            interval_expected,
            interval_received,
            interval_lost,
            interval_loss_rate,
            snapshot.duplicate_packets,
            snapshot.out_of_order_packets,
            snapshot.late_packets,
            self._stats["controlResponses"],
            self._stats["controlResponseBytes"],
            self._stats["controlTimeouts"],
            self._stats["controlUnexpectedResponses"],
            self._stats["handshakeAckWrites"],
            self._stats["handshakeAckWriteBytes"],
            self._stats["handshakeAckRepeatedFrames"],
            self._stats["handshakeAckPartialTimeouts"],
            self._stats["streamHandshakeFrames"],
        )
        self._last_summary_snapshot = snapshot

    async def _flush(self, client: Any) -> None:
        if hasattr(client, "flush"):
            await asyncio.to_thread(client.flush)

    async def _close_client(self, client: Any) -> None:
        try:
            await asyncio.to_thread(client.close)
        except Exception:
            LOG.exception("Failed to close serial client: target=%s", _safe_log_text(self._target))

    async def stop(self) -> None:
        self._stopping = True
        LOG.info("Stopping serial adapter: target=%s captureStarted=%s", _safe_log_text(self._target), self._capture_started)
        if self._client is not None and self._capture_started:
            await self._send_stop_best_effort("service_stop")
        if self._client is not None:
            await self._close_client(self._client)
