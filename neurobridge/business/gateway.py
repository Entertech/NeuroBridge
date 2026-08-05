from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import time
import uuid
from typing import Any

from ..algorithm.runner import AlgorithmRunner
from ..config import GatewayConfig
from ..ble.packets import DataWindow, WindowAssembler
from .recording import RecordingStore

LOG = logging.getLogger(__name__)
PROTOCOL_VERSION = "1.0"
STREAMS = frozenset({"eeg", "hr", "eeg.raw", "hr.raw", "status"})


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
    replay_task: asyncio.Task | None = None


@dataclass(eq=False)
class ClientSession:
    subscriptions: dict[str, Subscription] = field(default_factory=dict)
    replay_algorithm: dict | None = None
    replay_algorithm_timestamp: int | None = None


class Gateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.boot_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:4]}"
        self.assembler = WindowAssembler()
        self.store = RecordingStore(config.recording.directory)
        self.algorithm = AlgorithmRunner(config.algorithm)
        self.status: dict[str, Any] = {"connectionState": "disconnected", "wearState": "unknown", "batteryPercent": None, "signalQuality": None, "algorithmState": "unavailable"}
        self.sessions: set[Any] = set()
        self.latest_algorithm: dict | None = None
        self.latest_algorithm_timestamp: int | None = None
        self._window_flush_task: asyncio.Task | None = None
        self._window_flush_deadline_ms: int | None = None

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

    async def stop(self) -> None:
        await self._cancel_window_flush()
        await self.algorithm.stop()
        self.store.stop()

    async def on_device_ready(self) -> None:
        """Start a fresh local algorithm session before publishing connected state."""
        await self.algorithm.initialize()
        self.status["algorithmState"] = "ready" if self.algorithm.available else ("error" if self.algorithm.error else "unavailable")

    async def update_status(self, name: str, value: object) -> None:
        previous = self.status.get(name)
        self.status[name] = value
        if name == "connectionState" and value == "connected" and previous != "connected":
            self.store.start()
        if name == "connectionState" and value == "disconnected" and previous != "disconnected":
            await self._cancel_window_flush()
            last = self.assembler.flush()
            if last:
                await self.publish_window(last)
            await self.algorithm.stop()
            self.status["algorithmState"] = "unavailable"
            self.store.stop()
        if previous != value:
            await self.broadcast_status()

    async def receive_packet(self, characteristic: str, value: bytes) -> None:
        for window in self.assembler.add(characteristic, value):
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
        # A disabled/unready algorithm does not invalidate correctly received raw data.
        if self.algorithm.available:
            reasons.extend(algorithm_reasons)
        valid = not reasons
        if raw:
            self.store.save_raw(timestamp_ms=window.end_ms, valid=valid, invalid_reasons=reasons, payload=raw)
        if algorithm_payload:
            self.latest_algorithm, self.latest_algorithm_timestamp = algorithm_payload, window.end_ms
            self.store.save_algorithm(timestamp_ms=window.end_ms, valid=valid, invalid_reasons=reasons, algorithm=algorithm_payload)
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
        request_id: str | None = None
        try:
            request = self.parse_request(raw)
            request_id, action, params = request["requestId"], request["action"], request["params"]
            if action == "getStatus":
                self.validate_params(params, set())
                result = self.status_result()
            elif action == "getLatest":
                self.validate_params(params, {"streams"})
                result = self.get_latest(session, params)
            elif action == "subscribe":
                self.validate_params(params, {"streams", "includeInvalid"})
                result = await self.subscribe(session, params, send)
            elif action == "unsubscribe":
                self.validate_params(params, {"subscriptionId"})
                result = await self.unsubscribe(session, params)
            else:
                raise ProtocolError(400, "INVALID_REQUEST", "Unknown action.", details={"action": action})
            await send(envelope(200, {"requestId": request_id, "action": action, "result": result}))
        except ProtocolError as error:
            await send(self.error(request_id, error))
        except Exception:
            LOG.exception("Request handling failed")
            await send(self.error(request_id, ProtocolError(500, "INTERNAL_ERROR", "Gateway request failed.", True)))

    def get_latest(self, session: ClientSession, params: dict) -> dict:
        streams = params.get("streams", ["eeg", "hr"])
        self.validate_streams(streams, allowed={"eeg", "hr"})
        if not self.live and not self.replay_available:
            raise ProtocolError(503, "REPLAY_NOT_AVAILABLE", "No replay data is available.", True)
        unavailable = set(streams) - self.available_streams()
        if unavailable:
            raise ProtocolError(409, "STREAM_NOT_AVAILABLE", "One or more streams are unavailable.", details={"streams": sorted(unavailable)})
        algorithm, timestamp = self.latest_algorithm, self.latest_algorithm_timestamp
        if not self.live and self.replay_available:
            # Do not look ahead to the final recording result. `getLatest` follows
            # this WebSocket connection's replay cursor, updated immediately before
            # each replay event is delivered to the connection.
            algorithm, timestamp = session.replay_algorithm, session.replay_algorithm_timestamp
        if not algorithm or timestamp is None:
            return {"mode": self.mode(), "timestampMs": now_ms(), "valid": False, "payload": {}}
        return {"mode": self.mode(), "timestampMs": timestamp, "valid": True, "payload": self.filtered_payload({}, algorithm, frozenset(streams))}

    def validate_streams(self, streams: object, allowed: set[str] | None = None, require: bool = True) -> list[str]:
        if not isinstance(streams, list) or (require and not streams) or any(not isinstance(item, str) for item in streams):
            raise ProtocolError(400, "INVALID_REQUEST", "params.streams must be a non-empty string array.")
        unique = list(dict.fromkeys(streams))
        invalid = set(unique) - (allowed or STREAMS)
        if invalid:
            raise ProtocolError(409, "STREAM_NOT_AVAILABLE", "One or more streams are unavailable.", details={"streams": sorted(invalid)})
        return unique

    @staticmethod
    def validate_params(params: dict, allowed: set[str]) -> None:
        unknown = set(params) - allowed
        if unknown:
            raise ProtocolError(400, "INVALID_REQUEST", "Request contains unsupported params.", details={"params": sorted(unknown)})

    async def subscribe(self, session: ClientSession, params: dict, send: Any) -> dict:
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
            raise ProtocolError(503, "REPLAY_NOT_AVAILABLE", "No replay data is available.", True)
        unavailable = set(streams) - self.available_streams()
        if unavailable:
            raise ProtocolError(409, "STREAM_NOT_AVAILABLE", "One or more streams are unavailable.", details={"streams": sorted(unavailable)})
        subscription = Subscription(f"sub-{uuid.uuid4().hex}", frozenset(streams), include_invalid, send)
        session.subscriptions[subscription.id] = subscription
        if not self.live:
            subscription.replay_task = asyncio.create_task(self.replay(session, subscription))
        return {"subscriptionId": subscription.id, "streams": streams, "mode": self.mode(), "intervalMs": 600}

    async def unsubscribe(self, session: ClientSession, params: dict) -> dict:
        subscription_id = params.get("subscriptionId")
        if not isinstance(subscription_id, str):
            raise ProtocolError(400, "INVALID_REQUEST", "params.subscriptionId is required.")
        subscription = session.subscriptions.pop(subscription_id, None)
        if not subscription:
            raise ProtocolError(404, "SUBSCRIPTION_NOT_FOUND", "Subscription does not exist.")
        if subscription.replay_task:
            subscription.replay_task.cancel()
        return {"subscriptionId": subscription_id}

    async def replay(self, session: ClientSession, subscription: Subscription) -> None:
        events = self.store.events(self.config.recording.replay_recording_id or "")
        previous: int | None = None
        try:
            for item in events:
                if previous is not None:
                    await asyncio.sleep(max(0, item["timestampMs"] - previous) / 1000 / self.config.recording.replay_speed)
                previous = item["timestampMs"]
                if "algorithm" in item["payload"]:
                    # Multiple subscriptions on one connection must never make its
                    # `getLatest` cursor move backward.
                    if session.replay_algorithm_timestamp is None or item["timestampMs"] >= session.replay_algorithm_timestamp:
                        session.replay_algorithm = item["payload"]["algorithm"]
                        session.replay_algorithm_timestamp = item["timestampMs"]
                payload = self.filtered_payload(item["payload"], item["payload"].get("algorithm"), subscription.streams)
                if not payload or (not item["valid"] and not subscription.include_invalid):
                    continue
                if not item["valid"]:
                    payload["invalidReasons"] = item["invalidReasons"]
                await subscription.send(envelope(200, self.event_data("data", subscription.id, item["timestampMs"], "replay", item["valid"], payload)))
            await subscription.send(envelope(200, {"event": "replayEnded", "gatewayBootId": self.boot_id, "subjectId": self.config.recording.subject_id, "mode": "replay", "timestampMs": previous or now_ms(), "valid": True, "payload": {}, "recordingId": self.config.recording.replay_recording_id, "endedAtMs": now_ms()}))
        except asyncio.CancelledError:
            return

    async def close_session(self, session: ClientSession) -> None:
        for subscription in session.subscriptions.values():
            if subscription.replay_task:
                subscription.replay_task.cancel()
        session.subscriptions.clear()
        self.sessions.discard(session)
