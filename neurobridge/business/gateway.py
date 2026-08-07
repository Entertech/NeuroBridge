from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from ..algorithm.runner import AlgorithmRunner
from ..config import GatewayConfig
from ..ble.packets import DataWindow, WindowAssembler
from ..versioning import NORTHBOUND_PROTOCOL_VERSION
from .recording import RecordingStore

LOG = logging.getLogger(__name__)
PROTOCOL_VERSION = NORTHBOUND_PROTOCOL_VERSION
STREAMS = frozenset({"eeg", "hr", "eeg.raw", "hr.raw", "status"})
# These identifiers are part of the locked v0.2 B-side contract.  Keep them
# stable until a later, explicitly published protocol version replaces them.
REPLAY_NOT_AVAILABLE_REASON = "REPLAY_NOT_AVAILA设备"
STREAM_NOT_AVAILABLE_REASON = "STREAM_NOT_AVAILA设备"
REPLAY_DELIVERY_QUEUE_SIZE = 16
# A recording containing one event has no source timestamp gap to pace a
# restart. Yield briefly at the cycle boundary so it cannot become a busy loop.
REPLAY_CYCLE_MIN_PAUSE_SECONDS = 0.001


def now_ms() -> int:
    return int(time.time() * 1000)


def envelope(code: int, data: dict, message: str = "OK") -> dict:
    return {"protocolVersion": PROTOCOL_VERSION, "code": code, "data": data, "message": message}


class ProtocolError(Exception):
    def __init__(self, code: int, reason: str, message: str, retryable: bool = False, details: dict | None = None) -> None:
        self.code, self.reason, self.message, self.retryable, self.details = code, reason, message, retryable, details or {}


@dataclass(eq=False)
class Subscription:
    id: str
    streams: frozenset[str]
    include_invalid: bool
    send: Any
    replay_outbox: asyncio.Queue[dict] = field(default_factory=lambda: asyncio.Queue(maxsize=REPLAY_DELIVERY_QUEUE_SIZE))
    replay_delivery_task: asyncio.Task | None = None


@dataclass(eq=False)
class ClientSession:
    subscriptions: dict[str, Subscription] = field(default_factory=dict)


class Gateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.boot_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:4]}"
        self.assembler = WindowAssembler()
        self.store = RecordingStore(config.recording.directory)
        self.algorithm = AlgorithmRunner(config.algorithm)
        self.status: dict[str, Any] = {"connectionState": "disconnected", "wearState": "unknown", "batteryPercent": None, "signalQuality": None, "algorithmState": "unavailable"}
        # This is deliberately an operational-only field: the released B-side
        # status contract is unchanged.  Linux operators can inspect it in the
        # durable gateway log, matching the failure detail shown by the macOS
        # POC control page.
        self.connection_error: str | None = None
        self.sessions: set[Any] = set()
        self.latest_algorithm: dict | None = None
        self.latest_algorithm_timestamp: int | None = None
        self.window_observer: Callable[[DataWindow, dict | None, list[str], bool], Awaitable[None]] | None = None
        self._window_flush_task: asyncio.Task | None = None
        self._window_flush_deadline_ms: int | None = None
        self._replay_task: asyncio.Task | None = None
        self._replay_algorithm: dict | None = None
        self._replay_algorithm_timestamp: int | None = None

    @property
    def live(self) -> bool:
        return self.status["connectionState"] == "connected"

    @property
    def replay_available(self) -> bool:
        return self.store.has_recording(self.config.recording.replay_recording_id)

    def mode(self) -> str:
        return "live" if self.live else "replay"

    async def start(self) -> None:
        # The local algorithm is session scoped and must be initialized only after
        # a Flowtime connection has subscribed all notifications and started capture.
        self.status["algorithmState"] = "unavailable"
        LOG.info("Gateway started: bootId=%s recordingDirectory=%s networkMode=%s", self.boot_id, self.config.recording.directory, self.config.network.mode)

    async def stop(self) -> None:
        await self._cancel_window_flush()
        await self._stop_replay()
        await self.algorithm.stop()
        self.store.stop()
        LOG.info("Gateway stopped: bootId=%s", self.boot_id)

    async def on_device_ready(self) -> None:
        """Start a fresh local algorithm session before publishing connected state."""
        await self.algorithm.initialize()
        self.status["algorithmState"] = "ready" if self.algorithm.available else ("error" if self.algorithm.error else "unavailable")
        LOG.info("Device capture initialized: algorithmState=%s", self.status["algorithmState"])

    async def update_status(self, name: str, value: object) -> None:
        previous = self.status.get(name)
        self.status[name] = value
        if name == "connectionState" and value == "connected" and previous != "connected":
            self.connection_error = None
            await self._stop_replay()
            recording_id = self.store.start(now_ms())
            LOG.info("Headband connected; recording started: recordingId=%s", recording_id)
        if name == "connectionState" and value == "disconnected" and previous != "disconnected":
            await self._cancel_window_flush()
            last = self.assembler.flush()
            if last:
                await self.publish_window(last)
            await self.algorithm.stop()
            self.status["algorithmState"] = "unavailable"
            self.store.stop()
            LOG.info("Headband disconnected; recording stopped")
        if previous != value:
            if name != "connectionState":
                LOG.info("Gateway status changed: %s=%s", name, value)
            await self.broadcast_status()

    async def update_connection_error(self, error: str) -> None:
        """Record the latest BLE setup failure for Linux operational diagnosis.

        ``FlowtimeAdapter`` retries internally, so surfacing the exception here
        must not terminate the gateway or alter the published northbound schema.
        """
        self.connection_error = error
        LOG.warning("Headband connection attempt failed: %s", error)

    async def receive_packet(self, characteristic: str, value: bytes) -> None:
        received_at_ms = now_ms()
        raw_stream = {"ff31": "eeg", "ff51": "hr_native", "ff52": "hr"}.get(characteristic)
        if raw_stream:
            window_start_ms = received_at_ms - received_at_ms % self.assembler.interval_ms
            self.store.save_raw_packet(
                stream=raw_stream,
                received_at_ms=received_at_ms,
                window_start_ms=window_start_ms,
                window_end_ms=window_start_ms + self.assembler.interval_ms,
                value=value,
            )
        for window in self.assembler.add(characteristic, value, received_at_ms):
            await self.publish_window(window)
        self._schedule_window_flush()

    def _schedule_window_flush(self) -> None:
        deadline = self.assembler.window_end_ms
        if deadline is None:
            return
        if self._window_flush_task and not self._window_flush_task.done() and self._window_flush_deadline_ms == deadline:
            return
        if self._window_flush_task and not self._window_flush_task.done():
            self._window_flush_task.cancel()
        self._window_flush_deadline_ms = deadline
        self._window_flush_task = asyncio.create_task(self._flush_window_at(deadline))

    async def _flush_window_at(self, deadline_ms: int) -> None:
        try:
            await asyncio.sleep(max(0, deadline_ms - now_ms()) / 1000)
            if self._window_flush_deadline_ms != deadline_ms:
                return
            for window in self.assembler.flush_until(deadline_ms):
                await self.publish_window(window)
        except asyncio.CancelledError:
            return
        finally:
            if self._window_flush_task is asyncio.current_task():
                self._window_flush_task = None
                self._window_flush_deadline_ms = None

    async def _cancel_window_flush(self) -> None:
        task, self._window_flush_task = self._window_flush_task, None
        self._window_flush_deadline_ms = None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def publish_window(self, window: DataWindow) -> None:
        raw = window.raw_payload()
        reasons = list(window.reasons)
        algorithm_payload, algorithm_reasons = await self.algorithm.evaluate(window)
        if self.algorithm.error and self.status["algorithmState"] != "error":
            await self.update_status("algorithmState", "error")
        # A disabled/unready algorithm does not invalidate correctly received raw data.
        if self.algorithm.available:
            reasons.extend(algorithm_reasons)
        valid = not reasons
        if algorithm_payload:
            self.store.save_algorithm_events(
                algorithm=algorithm_payload,
                computed_at_ms=now_ms(),
                eeg_source=self.store.source_reference(window.eeg, window_start_ms=window.start_ms, window_end_ms=window.end_ms),
                hr_source=self.store.source_reference(window.hr_raw, window_start_ms=window.start_ms, window_end_ms=window.end_ms),
                valid=valid,
                invalid_reasons=reasons,
            )
            if valid:
                self.latest_algorithm, self.latest_algorithm_timestamp = algorithm_payload, window.end_ms
        if self.window_observer:
            try:
                await self.window_observer(window, algorithm_payload, reasons, valid)
            except Exception:
                LOG.exception("Capture window observer failed")
        for session in tuple(self.sessions):
            for subscription in tuple(session.subscriptions.values()):
                payload = self.filtered_payload(raw, algorithm_payload, subscription.streams)
                if not payload or (not valid and not subscription.include_invalid):
                    continue
                if not valid:
                    payload["invalidReasons"] = reasons
                await subscription.send(envelope(200, self.event_data("data", subscription.id, window.end_ms, "live", valid, payload)))

    def filtered_payload(self, raw: dict, algorithm_payload: dict | None, streams: frozenset[str]) -> dict:
        payload: dict = {}
        if "eeg.raw" in streams and "eegRaw" in raw:
            payload["eegRaw"] = raw["eegRaw"]
        if "hr.raw" in streams and "hrRaw" in raw:
            payload["hrRaw"] = raw["hrRaw"]
        if algorithm_payload:
            algorithm: dict = {}
            if "eeg" in streams:
                algorithm.update({key: value for key, value in algorithm_payload.items() if key not in {"hr", "pressure", "coherence", "arousal"}})
            if "hr" in streams:
                algorithm.update({key: value for key, value in algorithm_payload.items() if key in {"hr", "pressure", "coherence", "arousal"}})
            if algorithm:
                payload["algorithm"] = algorithm
        return payload

    def event_data(self, event: str, subscription_id: str | None, timestamp_ms: int, mode: str, valid: bool, payload: dict) -> dict:
        data = {"event": event, "gatewayBootId": self.boot_id, "subjectId": self.config.recording.subject_id, "mode": mode, "timestampMs": timestamp_ms, "valid": valid, "payload": payload}
        if subscription_id:
            data["subscriptionId"] = subscription_id
        return data

    def status_result(self) -> dict:
        return {"gatewayBootId": self.boot_id, "subjectId": self.config.recording.subject_id, "mode": self.mode(), **self.status, "availableStreams": sorted(self.available_streams()), "serverTimeMs": now_ms()}

    def available_streams(self) -> set[str]:
        available = {"status"}
        if self.live or self.replay_available:
            available.update({"eeg.raw", "hr.raw"})
        if self.algorithm.available:
            available.update({"eeg", "hr"})
        if not self.live and self.replay_available:
            for event in self.store.events(self.config.recording.replay_recording_id or ""):
                algorithm = event["payload"].get("algorithm", {})
                if any(key in algorithm for key in ("eeg", "sleep", "relaxation", "pleasure", "attention", "flow")):
                    available.add("eeg")
                if any(key in algorithm for key in ("hr", "pressure", "coherence", "arousal")):
                    available.add("hr")
        return available

    async def broadcast_status(self) -> None:
        for session in tuple(self.sessions):
            for subscription in tuple(session.subscriptions.values()):
                if "status" in subscription.streams:
                    payload = {"status": {name: self.status[name] for name in ("connectionState", "wearState", "batteryPercent", "signalQuality")}}
                    await subscription.send(envelope(200, self.event_data("status", subscription.id, now_ms(), self.mode(), True, payload)))

    def error(self, request_id: str | None, error: ProtocolError) -> dict:
        data = {"reason": error.reason, "retryable": error.retryable, "details": error.details}
        if request_id:
            data["requestId"] = request_id
        return envelope(error.code, data, error.message)

    def parse_request(self, raw: str) -> dict:
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProtocolError(400, "INVALID_REQUEST", "Request is not valid JSON.") from exc
        if not isinstance(request, dict) or set(request) != {"protocolVersion", "messageType", "requestId", "action", "params"}:
            raise ProtocolError(400, "INVALID_REQUEST", "Request root fields are invalid.")
        if request["protocolVersion"] != PROTOCOL_VERSION:
            raise ProtocolError(505, "UNSUPPORTED_VERSION", "Protocol version is not supported.")
        if request["messageType"] != "request" or not isinstance(request["requestId"], str) or not isinstance(request["params"], dict):
            raise ProtocolError(400, "INVALID_REQUEST", "Request fields are invalid.")
        return request

    async def handle(self, session: ClientSession, raw: str, send: Any) -> None:
        # The WebSocket adapter registers sessions on connect.  Keeping this
        # here as well makes the gateway API safe for other adapters and tests.
        self.sessions.add(session)
        request_id: str | None = None
        try:
            request = self.parse_request(raw)
            request_id, action, params = request["requestId"], request["action"], request["params"]
            start_replay_after_response = False
            if action == "getStatus":
                self.validate_params(params, set())
                result = self.status_result()
            elif action == "getLatest":
                self.validate_params(params, {"streams"})
                result = self.get_latest(session, params, start_replay=False)
                start_replay_after_response = not self.live and self.replay_available
            elif action == "subscribe":
                self.validate_params(params, {"streams", "includeInvalid"})
                result = await self.subscribe(session, params, send, start_replay=False)
                start_replay_after_response = not self.live and self.replay_available
            elif action == "unsubscribe":
                self.validate_params(params, {"subscriptionId"})
                result = await self.unsubscribe(session, params)
            else:
                raise ProtocolError(400, "INVALID_REQUEST", "Unknown action.", details={"action": action})
            await send(envelope(200, {"requestId": request_id, "action": action, "result": result}))
            if start_replay_after_response:
                self._start_replay_if_needed()
        except ProtocolError as error:
            await send(self.error(request_id, error))
        except Exception:
            LOG.exception("Request handling failed")
            await send(self.error(request_id, ProtocolError(500, "INTERNAL_ERROR", "Gateway request failed.", True)))

    def get_latest(self, session: ClientSession, params: dict, *, start_replay: bool = True) -> dict:
        streams = params.get("streams", ["eeg", "hr"])
        self.validate_streams(streams, allowed={"eeg", "hr"})
        if not self.live and not self.replay_available:
            raise ProtocolError(503, REPLAY_NOT_AVAILABLE_REASON, "No replay data is available.", True)
        unavailable = set(streams) - self.available_streams()
        if unavailable:
            raise ProtocolError(409, STREAM_NOT_AVAILABLE_REASON, "One or more streams are unavailable.", details={"streams": sorted(unavailable)})
        algorithm, timestamp = self.latest_algorithm, self.latest_algorithm_timestamp
        if not self.live and self.replay_available:
            if start_replay:
                self._start_replay_if_needed()
            algorithm, timestamp = self.latest_replay_algorithm()
        if not algorithm or timestamp is None:
            return {"mode": self.mode(), "timestampMs": now_ms(), "valid": False, "payload": {}}
        return {"mode": self.mode(), "timestampMs": timestamp, "valid": True, "payload": self.filtered_payload({}, algorithm, frozenset(streams))}

    def validate_streams(self, streams: object, allowed: set[str] | None = None, require: bool = True) -> list[str]:
        if not isinstance(streams, list) or (require and not streams) or any(not isinstance(item, str) for item in streams):
            raise ProtocolError(400, "INVALID_REQUEST", "params.streams must be a non-empty string array.")
        unique = list(dict.fromkeys(streams))
        invalid = set(unique) - (allowed or STREAMS)
        if invalid:
            raise ProtocolError(409, STREAM_NOT_AVAILABLE_REASON, "One or more streams are unavailable.", details={"streams": sorted(invalid)})
        return unique

    def latest_replay_algorithm(self) -> tuple[dict | None, int | None]:
        """Use the gateway's active replay cursor, or the latest valid result before it advances."""
        if self._replay_algorithm is not None and self._replay_algorithm_timestamp is not None:
            return self._replay_algorithm, self._replay_algorithm_timestamp
        for item in reversed(self.store.events(self.config.recording.replay_recording_id or "")):
            algorithm = item["payload"].get("algorithm")
            if item["valid"] and algorithm:
                return algorithm, item["timestampMs"]
        return None, None

    @staticmethod
    def validate_params(params: dict, allowed: set[str]) -> None:
        unknown = set(params) - allowed
        if unknown:
            raise ProtocolError(400, "INVALID_REQUEST", "Request contains unsupported params.", details={"params": sorted(unknown)})

    async def subscribe(self, session: ClientSession, params: dict, send: Any, *, start_replay: bool = True) -> dict:
        streams = self.validate_streams(params.get("streams"))
        include_invalid = params.get("includeInvalid", False)
        if not isinstance(include_invalid, bool):
            raise ProtocolError(400, "INVALID_REQUEST", "params.includeInvalid must be boolean.")
        if len(session.subscriptions) >= 4:
            raise ProtocolError(429, "RATE_LIMITED", "Subscription limit exceeded.", True)
        already_subscribed = set().union(*(item.streams for item in session.subscriptions.values())) if session.subscriptions else set()
        duplicate_streams = set(streams) & already_subscribed
        if duplicate_streams:
            raise ProtocolError(429, "RATE_LIMITED", "A stream is already subscribed on this connection.", True, {"streams": sorted(duplicate_streams)})
        if not self.live and not self.replay_available:
            raise ProtocolError(503, REPLAY_NOT_AVAILABLE_REASON, "No replay data is available.", True)
        unavailable = set(streams) - self.available_streams()
        if unavailable:
            raise ProtocolError(409, STREAM_NOT_AVAILABLE_REASON, "One or more streams are unavailable.", details={"streams": sorted(unavailable)})
        subscription = Subscription(f"sub-{uuid.uuid4().hex}", frozenset(streams), include_invalid, send)
        self.sessions.add(session)
        session.subscriptions[subscription.id] = subscription
        subscription.replay_delivery_task = asyncio.create_task(self._deliver_replay(session, subscription))
        if start_replay and not self.live:
            self._start_replay_if_needed()
        return {"subscriptionId": subscription.id, "streams": streams, "mode": self.mode(), "intervalMs": 600}

    async def unsubscribe(self, session: ClientSession, params: dict) -> dict:
        subscription_id = params.get("subscriptionId")
        if not isinstance(subscription_id, str):
            raise ProtocolError(400, "INVALID_REQUEST", "params.subscriptionId is required.")
        subscription = session.subscriptions.get(subscription_id)
        if not subscription:
            raise ProtocolError(404, "SUBSCRIPTION_NOT_FOUND", "Subscription does not exist.")
        await self._remove_subscription(session, subscription)
        return {"subscriptionId": subscription_id}

    def _start_replay_if_needed(self) -> None:
        """Start one replay clock for the gateway after the first B-side data request."""
        if not self.sessions or self.live or not self.replay_available or (self._replay_task and not self._replay_task.done()):
            return
        self._reset_replay_progress()
        self._replay_task = asyncio.create_task(self._replay())
        LOG.info("Replay started: recordingId=%s", self.config.recording.replay_recording_id)

    async def _stop_replay(self) -> None:
        task, self._replay_task = self._replay_task, None
        self._reset_replay_progress()
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            LOG.info("Replay stopped")

    def _reset_replay_progress(self) -> None:
        self._replay_algorithm = None
        self._replay_algorithm_timestamp = None

    def _replay_should_continue(self) -> bool:
        return not self.live and bool(self.sessions)

    def _subscription_entries(self) -> tuple[tuple[ClientSession, Subscription], ...]:
        return tuple((session, subscription) for session in tuple(self.sessions) for subscription in tuple(session.subscriptions.values()))

    def _queue_replay_message(self, session: ClientSession, subscription: Subscription, message: dict) -> None:
        """Do not let a slow or failed client stall the single replay clock."""
        if session.subscriptions.get(subscription.id) is not subscription:
            return
        try:
            subscription.replay_outbox.put_nowait(message)
        except asyncio.QueueFull:
            LOG.warning("Replay subscriber backlog exceeded limit; dropping subscriptionId=%s", subscription.id)
            if subscription.replay_delivery_task:
                subscription.replay_delivery_task.cancel()

    async def _deliver_replay(self, session: ClientSession, subscription: Subscription) -> None:
        try:
            while True:
                message = await subscription.replay_outbox.get()
                await subscription.send(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.warning("Replay subscriber delivery failed; dropping subscriptionId=%s", subscription.id, exc_info=True)
        finally:
            if session.subscriptions.get(subscription.id) is subscription:
                session.subscriptions.pop(subscription.id, None)

    async def _remove_subscription(self, session: ClientSession, subscription: Subscription) -> None:
        if session.subscriptions.get(subscription.id) is subscription:
            session.subscriptions.pop(subscription.id, None)
        task = subscription.replay_delivery_task
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _replay(self) -> None:
        events = self.store.events(self.config.recording.replay_recording_id or "")
        try:
            if not events:
                LOG.warning("Replay recording contains no events: recordingId=%s", self.config.recording.replay_recording_id)
                return
            cycle = 0
            while self._replay_should_continue():
                cycle += 1
                previous: int | None = None
                self._reset_replay_progress()
                for item in events:
                    if not self._replay_should_continue():
                        return
                    if previous is not None:
                        await asyncio.sleep(max(0, item["timestampMs"] - previous) / 1000 / self.config.recording.replay_speed)
                    if not self._replay_should_continue():
                        return
                    previous = item["timestampMs"]
                    if item["valid"] and "algorithm" in item["payload"]:
                        self._replay_algorithm = item["payload"]["algorithm"]
                        self._replay_algorithm_timestamp = item["timestampMs"]
                    for session, subscription in self._subscription_entries():
                        payload = self.filtered_payload(item["payload"], item["payload"].get("algorithm"), subscription.streams)
                        if not payload or (not item["valid"] and not subscription.include_invalid):
                            continue
                        if not item["valid"]:
                            payload["invalidReasons"] = item["invalidReasons"]
                        self._queue_replay_message(session, subscription, envelope(200, self.event_data("data", subscription.id, item["timestampMs"], "replay", item["valid"], payload)))
                if not self._replay_should_continue():
                    return
                ended = {"event": "replayEnded", "gatewayBootId": self.boot_id, "subjectId": self.config.recording.subject_id, "mode": "replay", "timestampMs": previous or now_ms(), "valid": True, "payload": {}, "recordingId": self.config.recording.replay_recording_id, "endedAtMs": now_ms()}
                for session, subscription in self._subscription_entries():
                    self._queue_replay_message(session, subscription, envelope(200, ended))
                LOG.info("Replay cycle ended; restarting: recordingId=%s cycle=%s", self.config.recording.replay_recording_id, cycle)
                await asyncio.sleep(REPLAY_CYCLE_MIN_PAUSE_SECONDS)
        except asyncio.CancelledError:
            return
        finally:
            if self._replay_task is asyncio.current_task():
                self._replay_task = None
            self._reset_replay_progress()

    async def close_session(self, session: ClientSession) -> None:
        for subscription in tuple(session.subscriptions.values()):
            await self._remove_subscription(session, subscription)
        self.sessions.discard(session)
        if not self.sessions:
            await self._stop_replay()
