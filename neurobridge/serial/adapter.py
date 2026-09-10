"""Serial headset discovery, framing, control responses, and packet-loss telemetry."""

from __future__ import annotations

import asyncio
from glob import glob
import logging
from pathlib import Path
import re
import time
from typing import Any, Awaitable, Callable, Iterable

from ..config import SerialConfig
from ..device.packet import DevicePacket, wall_clock_ms

LOG = logging.getLogger(__name__)
START_COMMAND = b"\xE1"
STOP_COMMAND = b"\xE0"
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


from ..adapters.parsers.sequence import LossSnapshot, SequenceObservation, SequenceLossTracker

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
        raw_chunk: Callable[[bytes, int], Awaitable[None]] | None = None,
        external_control: bool = False,
        external_start: Callable[[bool], Awaitable[bool]] | None = None,
        external_stop: Callable[[], Awaitable[None]] | None = None,
        prepare_algorithm: Callable[[], Awaitable[bool]] | None = None,
        release_algorithm: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.config = config
        self.packet = packet
        self.status = status
        self.device_ready = device_ready
        self.error = error
        self.candidate_provider = candidate_provider
        self.serial_factory = serial_factory
        self.identity_provider = identity_provider
        self.raw_chunk = raw_chunk
        self.external_control = external_control
        self.external_start = external_start
        self.external_stop = external_stop
        self.prepare_algorithm = prepare_algorithm
        self.release_algorithm = release_algorithm
        self._client: Any | None = None
        self._target: str | None = None
        self._stopping = False
        self._capture_started = False
        self._stop_sent = False
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
            "startedAtMonotonic": time.monotonic(),
            "lastSummaryAtMonotonic": 0.0,
        }

    async def _prepare_algorithm(self) -> bool:
        if self.prepare_algorithm is None:
            LOG.error("Serial algorithm preparation unavailable; E1 disabled")
            return False
        try:
            return bool(await self.prepare_algorithm())
        except Exception:
            LOG.exception("Serial algorithm preparation failed before device validation")
            return False

    async def run(self) -> None:
        attempt = 0
        while not self._stopping:
            attempt += 1
            phase = "discover"
            target_selected = False
            validation_failed = False
            # Preparation owns no recording or validated connection. It runs
            # concurrently with discovery/opening/observation and is reclaimed
            # on every unsuccessful attempt (including cancellation).
            preparation = asyncio.create_task(self._prepare_algorithm())
            try:
                self._reset_stats()
                await self.status("connectionState", "connecting")
                candidates = list(await asyncio.to_thread(self.candidate_provider, self.config))
                inventory = await asyncio.to_thread(_serial_discovery_inventory, self.config)
                LOG.info(
                    "Serial discovery started: attempt=%s candidates=%s deviceMode=%s "
                    "streamObservationTimeoutMs=%s byIdEntries=%s ttyACMEntries=%s ttyUSBEntries=%s "
                    "configuredPathExists=%s startupPolicy=direct_e1",
                    attempt, len(candidates), self.config.device, self.config.handshake_timeout_ms,
                    inventory["byIdEntries"], inventory["ttyACMEntries"], inventory["ttyUSBEntries"],
                    inventory["configuredPathExists"],
                )
                if not candidates:
                    LOG.warning(
                        "Serial discovery found no usable candidates: attempt=%s byIdEntries=%s "
                        "ttyACMEntries=%s ttyUSBEntries=%s configuredPathExists=%s nextRetrySeconds=%s",
                        attempt, inventory["byIdEntries"], inventory["ttyACMEntries"],
                        inventory["ttyUSBEntries"], inventory["configuredPathExists"],
                        self.config.reconnect_delay_seconds,
                    )
                    raise ConnectionError("No USB-derived serial candidates were found")
                opened_count = 0
                failures = []
                observed_stream = None
                for index, path in enumerate(candidates, start=1):
                    if self._stopping:
                        return
                    client = None
                    capture_requested = False
                    probe_buffer = bytearray()
                    probe_chunks = []
                    try:
                        phase = "candidate_open"
                        client = await self._open_candidate(path)
                        opened_count += 1
                        identity = await asyncio.to_thread(self.identity_provider, path)
                        LOG.info("Serial candidate discovered: path=%s vid=%s pid=%s driver=%s",
                                 _safe_log_text(path), _safe_log_text(identity.get("vid")),
                                 _safe_log_text(identity.get("pid")), _safe_log_text(identity.get("driver")))
                        LOG.info("Serial candidate opened: attempt=%s candidateIndex=%s candidateCount=%s path=%s",
                                 attempt, index, len(candidates), _safe_log_text(path))
                        phase = "existing_stream_observation"
                        observed_stream = await self._observe_existing_stream(client, path, buffer=probe_buffer, read_chunks=probe_chunks)
                        if observed_stream is None:
                            await self.status("connectionState", "validating")
                            phase = "algorithm_prepare"
                            if not await preparation:
                                raise ConnectionError("Local algorithm is not ready; serial E1 was not sent")
                            if self._stopping:
                                return
                            # Observe once more without clearing the driver buffer:
                            # a stream may have started while the algorithm warmed.
                            observed_stream = await self._observe_existing_stream(client, path, timeout_seconds=0.01, buffer=probe_buffer, read_chunks=probe_chunks)
                            if observed_stream is None:
                                if self._stopping:
                                    return
                                phase = "start_command_write"
                                capture_requested = True  # partial writes also need best-effort E0
                                await self._write_client_command(client, START_COMMAND, "start")
                                phase = "first_frame_validation"
                                observed_stream = await self._observe_existing_stream(
                                    client, path, timeout_seconds=self.config.data_timeout_seconds,
                                    buffer=probe_buffer, read_chunks=probe_chunks,
                                )
                        if observed_stream is None:
                            validation_failed = True
                            LOG.warning("Serial first-frame validation timed out: path=%s timeoutSeconds=%s",
                                        _safe_log_text(path), self.config.data_timeout_seconds)
                            continue
                        self._client, self._target = client, path
                        self._stop_sent = False
                        self._capture_started = True
                        target_selected = True
                        validation_failed = False
                        LOG.info(
                            "Serial target selected: path=%s selectionMode=%s matchBasis=valid_28_byte_frame",
                            _safe_log_text(path), "direct_e1" if capture_requested else "existing_valid_frame",
                        )
                        await self.status("connectionState", "validated")
                        break
                    except Exception as exc:
                        failures.append((path, exc))
                        LOG.warning("Serial candidate probe failed: attempt=%s path=%s phase=%s errorType=%s reason=%s",
                                    attempt, _safe_log_text(path), phase, type(exc).__name__, _safe_log_text(exc))
                    finally:
                        if client is not None and client is not self._client:
                            if capture_requested:
                                try:
                                    await self._write_client_command(client, STOP_COMMAND, "probe_cleanup_stop")
                                except Exception:
                                    LOG.exception("Serial probe cleanup E0 failed: path=%s", _safe_log_text(path))
                            await self._close_client(client)
                if self._client is None:
                    if validation_failed:
                        raise TimeoutError("Serial candidates opened but no valid 28-byte frame was received")
                    if failures:
                        failed_path, error = failures[-1]
                        raise ConnectionError(
                            f"Unable to {'open' if opened_count == 0 else 'probe'} any usable serial candidate; "
                            f"lastPath={_safe_log_text(failed_path)} lastErrorType={type(error).__name__} "
                            f"lastReason={_safe_log_text(error)}"
                        ) from error
                    raise ConnectionError("No serial candidate produced a valid frame")
                # E1 was already sent before validation, or the stream existed.
                # Adopt control ownership so session setup never sends it twice.
                if self.external_control:
                    if self.external_start is None or not await self.external_start(True):
                        raise ConnectionError("Session-bound DeviceControl did not adopt the serial stream")
                phase = "session_prepare"
                await preparation
                if self._stopping:
                    return
                algorithm_ready = await self.device_ready()
                if not algorithm_ready:
                    LOG.warning("Serial algorithm unavailable; adopting existing capture: target=%s", self._target)
                phase = "streaming"
                initial, received_at_ms = observed_stream
                await self._stream(initial, received_at_ms, initial_chunks=probe_chunks)
                if not self._stopping:
                    raise ConnectionError("Serial stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning("Serial connection failed: attempt=%s phase=%s target=%s errorType=%s reason=%s",
                            attempt, phase, _safe_log_text(self._target), type(exc).__name__, _safe_log_text(exc))
                if self.error:
                    await self.error(_safe_log_text(exc))
            finally:
                if not preparation.done():
                    preparation.cancel()
                await asyncio.gather(preparation, return_exceptions=True)
                if self._client is not None:
                    try:
                        if self._capture_started:
                            if self.external_control and self.external_stop is not None:
                                await self.external_stop()
                            await self._send_stop_best_effort("adapter_cleanup")
                        self._log_stats("disconnect")
                    finally:
                        await self._close_client(self._client)
                self._client = None
                self._target = None
                self._capture_started = False
                if self.release_algorithm is not None:
                    try:
                        await self.release_algorithm()
                    except Exception:
                        LOG.exception("Serial algorithm preparation cleanup failed")
                await self.status("connectionState", "disconnected" if target_selected else
                                  "validation_failed" if validation_failed else "not_connected")
            if not self._stopping:
                LOG.info("Serial reconnect scheduled: attempt=%s nextAttempt=%s delaySeconds=%s",
                         attempt, attempt + 1, self.config.reconnect_delay_seconds)
                await asyncio.sleep(self.config.reconnect_delay_seconds)

    async def _open_candidate(self, path: str) -> Any:
        opening = asyncio.create_task(asyncio.to_thread(self.serial_factory, path, self.config))
        try:
            return await asyncio.shield(opening)
        except asyncio.CancelledError:
            # Opening a COM/TTY happens in a thread; cancellation must still close
            # the late result rather than leaking a port handle.
            try:
                await self._close_client(await opening)
            except Exception:
                LOG.exception("Serial candidate open failed during cancellation")
            raise

    @staticmethod
    async def _io_call(function, *args):
        operation = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            # Never race a still-running read/write against E0 or close.
            await asyncio.gather(operation, return_exceptions=True)
            raise

    async def _observe_existing_stream(self, client: Any, path: str, *, timeout_seconds: float | None = None, buffer: bytearray | None = None,
                                       read_chunks: list[tuple[bytes, int]] | None = None) -> tuple[bytes, int] | None:
        """Return bytes and their read-boundary time for an existing valid stream."""

        buffer = bytearray() if buffer is None else buffer
        started = time.monotonic()
        deadline = started + (self.config.handshake_timeout_ms / 1000 if timeout_seconds is None else timeout_seconds)
        previous_timeout = getattr(client, "timeout", None)
        try:
            while not self._stopping and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                client.timeout = min(0.1, max(0.001, remaining))
                async with self._io_lock:
                    chunk = bytes(await self._io_call(client.read, 4096))
                if not chunk:
                    await asyncio.sleep(min(0.01, max(0.0, remaining)))
                    continue
                received_at_ms = wall_clock_ms()
                buffer.extend(chunk)
                if read_chunks is not None:
                    read_chunks.append((chunk, received_at_ms))
                if len(buffer) > self.config.max_buffer_bytes:
                    discarded = len(buffer) - self.config.max_buffer_bytes
                    del buffer[:discarded]
                    self._stats["bufferOverflows"] = int(self._stats["bufferOverflows"]) + 1
                    self._record_discarded_bytes(discarded, "probe_buffer_limit", len(buffer))
                    if read_chunks is not None:
                        while discarded:
                            value, stamp = read_chunks.pop(0)
                            removed = min(discarded, len(value))
                            discarded -= removed
                            if removed < len(value):
                                read_chunks.insert(0, (value[removed:], stamp))
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
            "nextAction=await_readiness_or_retry payloadLogged=false",
            _safe_log_text(path),
            int((time.monotonic() - started) * 1000),
            len(buffer),
        )
        return None

    async def _write_client_command(self, client: Any, command: bytes, name: str) -> None:
        started = time.monotonic()
        async with self._io_lock:
            if command == START_COMMAND and self._stopping:
                raise ConnectionError("Serial start cancelled by service stop")
            if command == STOP_COMMAND and client is self._client:
                if self._stop_sent:
                    return
                # All normal-session cleanup paths share this claim, including
                # cancellation before DeviceControl finishes adopting the stream.
                self._stop_sent = True
            written = await self._io_call(client.write, command)
            if written != len(command):
                raise OSError(f"Serial command write was incomplete: command={name} expected={len(command)} actual={written}")
            await self._flush(client)
        LOG.info("Serial command sent: command=%s commandBytes=%s durationMs=%s responseExpected=false success=true",
                 name, len(command), int((time.monotonic() - started) * 1000))

    async def _send_command(self, command: bytes, name: str) -> None:
        if self._client is None:
            raise ConnectionError(f"Serial command cannot be sent without a client: {name}")
        await self._write_client_command(self._client, command, name)

    async def control_write(self, command: bytes) -> None:
        """Write only a confirmed stream-control command for DeviceControl."""

        names = {START_COMMAND: "start", STOP_COMMAND: "stop"}
        try:
            name = names[bytes(command)]
        except KeyError as error:
            raise ValueError("Unsupported external serial control command") from error
        await self._send_command(bytes(command), name)

    async def _send_stop_best_effort(self, reason: str) -> None:
        async with self._stop_lock:
            if not self._capture_started:
                return
            # Claim the single stop attempt before awaiting I/O so concurrent
            # service-stop and adapter-cleanup paths cannot both send 0xE0.
            self._capture_started = False
            if self._stop_sent:
                return
            try:
                await self._send_command(STOP_COMMAND, "stop")
                LOG.info(
                    "Serial stop command completed: reason=%s responseExpected=false success=true",
                    reason,
                )
            except Exception:
                LOG.exception("Serial stop command failed: reason=%s", reason)

    async def _stream(self, initial: bytes, initial_received_at_ms: int | None = None, *,
                      initial_chunks: list[tuple[bytes, int]] | None = None) -> None:
        buffer = bytearray(initial)
        buffer_received_at_ms = initial_received_at_ms
        if initial and self.raw_chunk is not None:
            for value, stamp in initial_chunks or [(initial, initial_received_at_ms if initial_received_at_ms is not None else wall_clock_ms())]:
                await self.raw_chunk(value, stamp)
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
                    "invalidFrames=%s discardedBytes=%s bufferOverflows=%s bufferedBytes=%s",
                    _safe_log_text(self._target),
                    self.config.data_timeout_seconds,
                    self._stats["readBytes"],
                    self._stats["frames"],
                    self._stats["invalidFrames"],
                    self._stats["discardedBytes"],
                    self._stats["bufferOverflows"],
                    len(buffer),
                )
                raise TimeoutError(f"No valid serial data frame for {self.config.data_timeout_seconds:.3f} seconds")
            client = self._client
            if client is None:
                return
            async with self._io_lock:
                chunk = bytes(await self._io_call(client.read, 4096))
            if chunk:
                buffer_received_at_ms = wall_clock_ms()
                if self.raw_chunk is not None:
                    await self.raw_chunk(chunk, buffer_received_at_ms)
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
                keep = min(len(buffer), len(FRAME_HEADER) - 1)
                discarded = len(buffer) - keep
                if discarded:
                    del buffer[:discarded]
                    self._record_discarded_bytes(discarded, "frame_header_not_found", len(buffer))
                return parsed
            if offset:
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
            if self.raw_chunk is not None:
                # Raw-source production mode uses this scan only for physical
                # stream validity/timeouts. Application's parser owns signals.
                continue
            # Preserve the confirmed 28-byte transport frame independently of
            # the compatibility projections consumed by the existing algorithm.
            await self.packet(DevicePacket("serial", "serial.frame", frame, frame_received_at_ms))
            await self.packet(DevicePacket("serial", "ff31", frame[EEG_START:EEG_END], frame_received_at_ms))
            await self.packet(DevicePacket("serial", "ff51", frame[HR_OFFSET:HR_OFFSET + 1], frame_received_at_ms))

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
            "intervalLostPackets=%s intervalLossRatePercent=%.6f duplicates=%s outOfOrder=%s late=%s",
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
        )
        self._last_summary_snapshot = snapshot

    async def _flush(self, client: Any) -> None:
        if hasattr(client, "flush"):
            await self._io_call(client.flush)

    async def _close_client(self, client: Any) -> None:
        try:
            async with self._io_lock:
                await self._io_call(client.close)
        except Exception:
            LOG.exception("Failed to close serial client: target=%s", _safe_log_text(self._target))

    async def stop(self) -> None:
        self._stopping = True
        LOG.info("Stopping serial adapter: target=%s captureStarted=%s", _safe_log_text(self._target), self._capture_started)
        if self._client is not None and self._capture_started:
            if self.external_control and self.external_stop is not None:
                await self.external_stop()
            await self._send_stop_best_effort("service_stop")
        if self._client is not None:
            await self._close_client(self._client)
