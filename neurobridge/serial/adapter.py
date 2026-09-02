"""Serial headset discovery, framing, control responses, and packet-loss telemetry."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from glob import glob
import logging
from pathlib import Path
import time
from typing import Any, Awaitable, Callable, Iterable

from ..config import SerialConfig
from ..device.packet import DevicePacket, wall_clock_ms

LOG = logging.getLogger(__name__)
HANDSHAKE = bytes.fromhex("AA 55 01 01 01 01 6F")
START_COMMAND = b"\xE1"
STOP_COMMAND = b"\xE0"
EXPECTED_CONTROL_ACK = b"\x01"
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


def _classify_control_response(response: bytes) -> tuple[str, bool, int, int, str]:
    """Describe a short control response without logging its complete payload."""

    fixed_handshake_frames = response.count(HANDSHAKE)
    longest_handshake_prefix = _longest_handshake_prefix(response)
    expected_ack_01 = response == EXPECTED_CONTROL_ACK
    if expected_ack_01:
        classification = "single_byte_0x01"
    elif response == HANDSHAKE:
        classification = "fixed_handshake"
    elif fixed_handshake_frames:
        classification = "contains_fixed_handshake"
    elif longest_handshake_prefix:
        classification = "partial_handshake"
    elif len(response) == 1:
        classification = "other_single_byte"
    else:
        classification = "other_nonempty"
    single_byte_hex = f"{response[0]:02X}" if len(response) == 1 else "not_applicable"
    return (
        classification,
        expected_ack_01,
        fixed_handshake_frames,
        longest_handshake_prefix,
        single_byte_hex,
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
        return [config.device] if Path(config.device).exists() else []
    paths = sorted(glob("/dev/serial/by-id/*"))
    for candidate_type in config.candidate_types:
        paths.extend(sorted(glob(f"/dev/{candidate_type}*")))
    candidates: list[str] = []
    resolved_seen: set[str] = set()
    for value in paths:
        path = Path(value)
        try:
            resolved = str(path.resolve(strict=True))
        except OSError:
            continue
        if Path(resolved).name.startswith(tuple(config.candidate_types)) and resolved not in resolved_seen:
            candidates.append(str(path))
            resolved_seen.add(resolved)
    # Prefer persistent by-id aliases. Within each group, use the USB parent,
    # physical sysfs path, and interface number so multi-interface devices keep
    # the same probe order even if ttyACM/ttyUSB kernel indices change.
    return sorted(
        candidates,
        key=lambda candidate: _serial_candidate_order_key(candidate, serial_candidate_metadata(candidate)),
    )


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
    """Discover the first handshake device and expose BLE-compatible raw packets."""

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
    ) -> None:
        self.config = config
        self.packet = packet
        self.status = status
        self.device_ready = device_ready
        self.error = error
        self.candidate_provider = candidate_provider
        self.serial_factory = serial_factory
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
            try:
                await self.status("connectionState", "connecting")
                candidates = list(self.candidate_provider(self.config))
                inventory = _serial_discovery_inventory(self.config)
                LOG.info(
                    "Serial discovery started: attempt=%s candidates=%s deviceMode=%s handshakeTimeoutMs=%s "
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
                for index, path in enumerate(candidates, start=1):
                    identity = serial_candidate_metadata(path)
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
                phase = "handshake"
                opened_candidate_count = 0
                invalid_handshake_candidate_count = 0
                probe_failures: list[tuple[str, Exception]] = []
                for index, path in enumerate(candidates, start=1):
                    if self._stopping:
                        return
                    client = None
                    try:
                        client = await asyncio.to_thread(self.serial_factory, path, self.config)
                        opened_candidate_count += 1
                        LOG.info(
                            "Serial candidate opened: attempt=%s candidateIndex=%s candidateCount=%s path=%s",
                            attempt,
                            index,
                            len(candidates),
                            _safe_log_text(path),
                        )
                        if await self._await_handshake(client, path, attempt, index, len(candidates)):
                            self._client, self._target = client, path
                            target_selected = True
                            identity = serial_candidate_metadata(path)
                            LOG.info(
                                "Serial target selected: path=%s resolvedPath=%s vid=%s pid=%s usbSerial=%s "
                                "usbParent=%s interface=%s driver=%s physicalPath=%s",
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
                            # A valid device handshake proves that the USB-derived
                            # serial link is connected. Algorithm and start-command
                            # checks are represented by the following validation
                            # states instead of overloading "connected".
                            await self.status("connectionState", "connected")
                            break
                        invalid_handshake_candidate_count += 1
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
                    if invalid_handshake_candidate_count:
                        raise TimeoutError(
                            "Serial candidates opened but no valid fixed handshake was received"
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
                    raise TimeoutError("No serial candidate produced the fixed handshake")

                self._reset_stats()
                phase = "algorithm_initialize"
                LOG.info(
                    "Serial local algorithm preparation started: attempt=%s target=%s startCommandSent=false",
                    attempt,
                    _safe_log_text(self._target),
                )
                algorithm_ready = await self.device_ready()
                if not algorithm_ready:
                    await self.status("connectionState", "validation_failed")
                    LOG.error(
                        "Serial validation failed before start command: attempt=%s target=%s "
                        "reason=local_algorithm_not_ready startCommandSent=false",
                        attempt,
                        _safe_log_text(self._target),
                    )
                    raise ConnectionError("Local algorithm is not ready; serial start command was not sent")
                phase = "start_command_response"
                await self.status("connectionState", "validating")
                LOG.info(
                    "Serial device validation started: attempt=%s target=%s algorithmReady=true command=E1",
                    attempt,
                    _safe_log_text(self._target),
                )
                try:
                    initial = await self._send_control(START_COMMAND, "start")
                except Exception:
                    await self.status("connectionState", "validation_failed")
                    LOG.exception(
                        "Serial device validation failed: attempt=%s target=%s command=E1 "
                        "reason=control_write_or_read_error",
                        attempt,
                        _safe_log_text(self._target),
                    )
                    raise
                if not initial:
                    await self.status("connectionState", "validation_failed")
                    LOG.error(
                        "Serial device validation failed: attempt=%s target=%s command=E1 "
                        "reason=response_timeout timeoutMs=%s",
                        attempt,
                        _safe_log_text(self._target),
                        self.config.command_response_timeout_ms,
                    )
                    raise TimeoutError("Serial start command received no response")
                self._capture_started = True
                await self.status("connectionState", "validated")
                LOG.info(
                    "Serial device validation succeeded: attempt=%s target=%s command=E1 "
                    "responseBytes=%s policy=any_non_empty_response",
                    attempt,
                    _safe_log_text(self._target),
                    len(initial),
                )
                phase = "streaming"
                await self._stream(initial)
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

    async def _await_handshake(
        self,
        client: Any,
        path: str,
        attempt: int,
        index: int,
        candidate_count: int,
    ) -> bool:
        deadline = time.monotonic() + self.config.handshake_timeout_ms / 1000
        buffer = bytearray()
        read_bytes = 0
        reads = 0
        non_empty_reads = 0
        discarded_bytes = 0
        buffer_truncations = 0
        longest_prefix_bytes = 0
        started = time.monotonic()
        while not self._stopping and time.monotonic() < deadline:
            chunk = await asyncio.to_thread(client.read, 256)
            reads += 1
            if not chunk:
                continue
            non_empty_reads += 1
            read_bytes += len(chunk)
            buffer.extend(chunk)
            longest_prefix_bytes = max(longest_prefix_bytes, _longest_handshake_prefix(buffer))
            if len(buffer) > self.config.max_buffer_bytes:
                overflow = len(buffer) - self.config.max_buffer_bytes
                del buffer[:overflow]
                discarded_bytes += overflow
                buffer_truncations += 1
            offset = buffer.find(HANDSHAKE)
            if offset < 0:
                if len(buffer) > len(HANDSHAKE) - 1:
                    discarded = len(buffer) - (len(HANDSHAKE) - 1)
                    del buffer[:discarded]
                    discarded_bytes += discarded
                continue
            total_discarded_before_handshake = discarded_bytes + offset
            if total_discarded_before_handshake:
                LOG.warning(
                    "Serial unexpected bytes discarded before valid handshake: attempt=%s candidateIndex=%s "
                    "candidateCount=%s path=%s discardedBytes=%s longestHandshakePrefixBytes=%s "
                    "payloadLogged=false",
                    attempt,
                    index,
                    candidate_count,
                    _safe_log_text(path),
                    total_discarded_before_handshake,
                    longest_prefix_bytes,
                )
            await asyncio.to_thread(client.write, HANDSHAKE)
            await self._flush(client)
            LOG.info(
                "Serial handshake accepted and ACK sent: attempt=%s candidateIndex=%s candidateCount=%s path=%s "
                "reads=%s nonEmptyReads=%s readBytes=%s discardedBeforeHandshake=%s bufferTruncations=%s "
                "durationMs=%s traversalStopped=true",
                attempt,
                index,
                candidate_count,
                _safe_log_text(path),
                reads,
                non_empty_reads,
                read_bytes,
                total_discarded_before_handshake,
                buffer_truncations,
                int((time.monotonic() - started) * 1000),
            )
            return True
        duration_ms = int((time.monotonic() - started) * 1000)
        if read_bytes:
            LOG.warning(
                "Serial candidate produced bytes but no valid handshake: attempt=%s candidateIndex=%s "
                "candidateCount=%s path=%s reads=%s nonEmptyReads=%s readBytes=%s discardedBytes=%s "
                "bufferedBytes=%s bufferTruncations=%s longestHandshakePrefixBytes=%s "
                "invalidHandshakeObserved=true payloadLogged=false durationMs=%s",
                attempt,
                index,
                candidate_count,
                _safe_log_text(path),
                reads,
                non_empty_reads,
                read_bytes,
                discarded_bytes,
                len(buffer),
                buffer_truncations,
                longest_prefix_bytes,
                duration_ms,
            )
        else:
            LOG.warning(
                "Serial candidate produced no handshake bytes: attempt=%s candidateIndex=%s candidateCount=%s "
                "path=%s reads=%s timeoutMs=%s durationMs=%s",
                attempt,
                index,
                candidate_count,
                _safe_log_text(path),
                reads,
                self.config.handshake_timeout_ms,
                duration_ms,
            )
        return False

    async def _send_control(self, command: bytes, name: str) -> bytes:
        if self._client is None:
            return b""
        client = self._client
        started = time.monotonic()
        async with self._io_lock:
            if hasattr(client, "reset_input_buffer"):
                await asyncio.to_thread(client.reset_input_buffer)
            await asyncio.to_thread(client.write, command)
            await self._flush(client)
            previous_timeout = getattr(client, "timeout", None)
            client.timeout = self.config.command_response_timeout_ms / 1000
            try:
                # One byte is sufficient to confirm success and returns as soon
                # as the first response byte arrives. Drain only bytes already
                # buffered so a short response does not wait for the full timeout.
                response = bytes(await asyncio.to_thread(client.read, 1))
                waiting = min(max(int(getattr(client, "in_waiting", 0)), 0), 255)
                if response and waiting:
                    response += bytes(await asyncio.to_thread(client.read, waiting))
            finally:
                client.timeout = previous_timeout
        duration_ms = int((time.monotonic() - started) * 1000)
        if response:
            self._stats["controlResponses"] = int(self._stats["controlResponses"]) + 1
            self._stats["controlResponseBytes"] = int(self._stats["controlResponseBytes"]) + len(response)
            (
                classification,
                expected_ack_01,
                fixed_handshake_frames,
                longest_handshake_prefix,
                single_byte_hex,
            ) = _classify_control_response(response)
            log_response = LOG.warning if "handshake" in classification else LOG.info
            log_response(
                "Serial control response received: command=%s responseBytes=%s durationMs=%s "
                "responseClassification=%s expectedAck01=%s singleByteHex=%s "
                "fixedHandshakeFrames=%s longestHandshakePrefixBytes=%s success=true "
                "acceptedByCurrentPolicy=true payloadLogged=false",
                name,
                len(response),
                duration_ms,
                classification,
                str(expected_ack_01).lower(),
                single_byte_hex,
                fixed_handshake_frames,
                longest_handshake_prefix,
            )
        else:
            self._stats["controlTimeouts"] = int(self._stats["controlTimeouts"]) + 1
            LOG.warning(
                "Serial control response timed out: command=%s timeoutMs=%s durationMs=%s success=false",
                name,
                self.config.command_response_timeout_ms,
                duration_ms,
            )
        return response

    async def _send_stop_best_effort(self, reason: str) -> None:
        async with self._stop_lock:
            if not self._capture_started:
                return
            # Claim the single stop attempt before awaiting I/O so concurrent
            # service-stop and adapter-cleanup paths cannot both send 0xE0.
            self._capture_started = False
            try:
                response = await self._send_control(STOP_COMMAND, "stop")
                LOG.info(
                    "Serial stop command completed: reason=%s responseReceived=%s",
                    reason,
                    bool(response),
                )
            except Exception:
                LOG.exception("Serial stop command failed: reason=%s", reason)

    async def _stream(self, initial: bytes) -> None:
        buffer = bytearray(initial)
        last_frame_at = time.monotonic()
        self._stats["readBytes"] = int(self._stats["readBytes"]) + len(initial)
        while not self._stopping:
            parsed = await self._consume_frames(buffer)
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

    async def _consume_frames(self, buffer: bytearray) -> int:
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
            received_at_ms = wall_clock_ms()
            # Preserve the confirmed 28-byte transport frame independently of
            # the compatibility projections consumed by the existing algorithm.
            await self.packet(DevicePacket("serial", "serial.frame", frame, received_at_ms))
            await self.packet(DevicePacket("serial", "ff31", frame[EEG_START:EEG_END], received_at_ms))
            await self.packet(DevicePacket("serial", "ff51", frame[HR_OFFSET:HR_OFFSET + 1], received_at_ms))

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
            "controlResponses=%s controlResponseBytes=%s controlTimeouts=%s streamHandshakeFrames=%s",
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
