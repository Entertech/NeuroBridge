from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from ..domain.result import WindowResult
from ..ports.recording import RecordingRepository
from .subscriptions import SubscriptionFanout
from ..ports.errors import ProtocolError
from ..ports.northbound import NorthboundCodec

LOG = logging.getLogger(__name__)
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


def safe_log_text(value: object, limit: int = 512) -> str:
    return "".join(character if character.isprintable() else " " for character in str(value))[:limit]


@dataclass(eq=False)
class Subscription:
    id: str
    streams: frozenset[str]
    include_invalid: bool
    send: Any
    replay_outbox: asyncio.Queue[dict] = field(default_factory=lambda: asyncio.Queue(maxsize=REPLAY_DELIVERY_QUEUE_SIZE))
    replay_delivery_task: asyncio.Task | None = None
    live_delivery_task: asyncio.Task | None = None


@dataclass(eq=False)
class ClientSession:
    subscriptions: dict[str, Subscription] = field(default_factory=dict)


class GatewayApplication:
    """Own status, queries, subscriptions, and replay policy through injected ports."""

    def __init__(self, config, *, store, algorithm, snapshots, recording_factory, supports_replay: bool, replay_reader=None, project_window=None, wire: NorthboundCodec | None = None, live_connection_states=frozenset({"connected"}), initial_connection_state="disconnected") -> None:
        if wire is None:
            raise ValueError("A northbound codec must be supplied by Bootstrap")
        self.wire = wire
        self.config = config
        self.log = logging.getLogger(__name__)
        self._recording_factory = recording_factory
        self.supports_replay = supports_replay
        self.replay_reader = replay_reader if supports_replay else None
        self._replay_selection = (None, frozenset(), None, None)
        self._project_window = project_window
        self.boot_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:4]}"
        self.store = store
        self.recording_repository: RecordingRepository | None = None
        self.algorithm = algorithm
        self.live_connection_states = frozenset(live_connection_states)
        self.status: dict[str, Any] = {"connectionState": initial_connection_state, "wearState": "unknown", "batteryPercent": None, "signalQuality": None, "algorithmState": "unavailable"}
        # This is deliberately an operational-only field: the released B-side
        # status contract is unchanged.  Linux operators can inspect it in the
        # durable gateway log, matching the failure detail shown by the macOS
        # POC control page.
        self.connection_error: str | None = None
        self.sessions: set[Any] = set()
        self.latest_algorithm: dict | None = None
        self.latest_algorithm_timestamp: int | None = None
        self._last_live_update: float | None = None
        self.latest_snapshot = snapshots
        self.window_observer: Callable[..., Awaitable[None]] | None = None
        self._window_flush_task: asyncio.Task | None = None
        self._window_flush_deadline_ms: int | None = None
        self._replay_task: asyncio.Task | None = None
        self._active_replay_recording_id: str | None = None
        self._replay_algorithm: dict | None = None
        self._replay_algorithm_timestamp: int | None = None
        self._capture_stats: dict[str, int | None] = {
            "eegPackets": 0,
            "hrPackets": 0,
            "eegBytes": 0,
            "hrBytes": 0,
            "invalidPacketLengths": 0,
            "windows": 0,
            "invalidWindows": 0,
            "firstPacketAtMs": None,
            "lastDataAtMs": None,
            "lastSummaryAtMs": 0,
        }
        self._capture_final_summary_logged = False
        self.snapshot_overwrite_count = 0
        self.fanout = SubscriptionFanout()
        self.delivery_metrics = {"sent": 0, "failed": 0, "sendDurationMs": 0, "requests": 0,
                                 "lastSuccessfulAtMs": None}
        self.requests_by_action = {action: 0 for action in ("getStatus", "getLatest", "subscribe", "unsubscribe", "invalid")}
        self.stream_metrics = {stream: {"sent": 0, "failed": 0, "sendDurationMs": 0,
                                       "lastSuccessfulAtMs": None} for stream in STREAMS}

    async def start(self) -> None:
        # The local algorithm is session scoped and must be initialized only after
        # a Flowtime connection has subscribed all notifications and started capture.
        self.status["algorithmState"] = "unavailable"
        self.recording_repository = await asyncio.to_thread(self._recording_factory)
        self.log.info(
            "Gateway started: bootId=%s transport=%s recordingDirectory=%s networkMode=%s algorithmEnabled=%s",
            self.boot_id,
            self.config.data_source.type,
            self.config.recording.directory,
            self.config.network.mode,
            self.config.algorithm.enabled,
        )

    async def stop(self) -> None:
        for session in tuple(self.sessions):
            await self.close_session(session)
        await self._stop_replay()
        await self.algorithm.stop()
        if not self._capture_final_summary_logged and (
            int(self._capture_stats["eegPackets"] or 0) or int(self._capture_stats["hrPackets"] or 0)
        ):
            self._log_capture_summary("gateway_stop")
            self._capture_final_summary_logged = True
        recording_id = self.store.recording_id
        await asyncio.to_thread(self.store.stop)
        if self.recording_repository is not None:
            if recording_id:
                await self.recording_repository.close_session(recording_id)
            await self.recording_repository.close()
            self.recording_repository = None
        self.log.info("Gateway stopped: bootId=%s", self.boot_id)

    async def update_status(self, name: str, value: object) -> None:
        previous = self.status.get(name)
        was_live = name == "connectionState" and self._connection_state_is_live(previous)
        becomes_live = name == "connectionState" and self._connection_state_is_live(value)
        self.status[name] = value
        if name == "connectionState" and value in {"connected", "validated"} and previous != value:
            self.connection_error = None
        if becomes_live and not was_live:
            self.latest_snapshot.clear()
            self.latest_algorithm = self.latest_algorithm_timestamp = None
            self._last_live_update = None
            await self._stop_replay()
            self._reset_capture_stats()
            recording_id = await asyncio.to_thread(self.store.start, now_ms())
            self.log.info(
                "Device data path ready; recording started: transport=%s connectionState=%s recordingId=%s",
                self.config.data_source.type,
                value,
                recording_id,
            )
        if name == "connectionState" and value == "disconnected" and previous != "disconnected":
            await self.algorithm.stop()
            self.status["algorithmState"] = "unavailable"
            self._log_capture_summary("disconnected")
            self._capture_final_summary_logged = True
            recording_id = self.store.recording_id
            await asyncio.to_thread(self.store.stop)
            if recording_id and self.recording_repository is not None:
                await self.recording_repository.close_session(recording_id)
            self.log.info("Device disconnected; algorithm and recording stopped")
        if previous != value:
            if name == "connectionState":
                self.log.info(
                    "Connection state changed: previous=%s current=%s lastError=%s",
                    previous,
                    value,
                    self.connection_error,
                )
            else:
                self.log.info("Gateway status changed: %s=%s", name, value)
            await self.broadcast_status()

    async def publish_window_result(self, result: WindowResult) -> None:
        """Wire-compatible publication boundary for the new application service."""

        if not self.supports_replay and result.mode != "live":
            raise ValueError("Serial headset results must always use live mode")
        self._last_live_update = time.monotonic()
        algorithm_payload = dict(result.algorithm_result.metrics) or None
        if result.valid and algorithm_payload:
            self.latest_algorithm = algorithm_payload
            self.latest_algorithm_timestamp = result.batch.window_end_ms
        raw, source_refs, signals_by_type = self._project_window(result)
        for signal_type, signals in signals_by_type.items():
            self._capture_stats[f"{signal_type}Packets"] += len(signals)
            self._capture_stats[f"{signal_type}Bytes"] += sum(len(s.samples) for s in signals)
            if signals:
                self._capture_stats["firstPacketAtMs"] = self._capture_stats["firstPacketAtMs"] or signals[0].received_at_ms
                self._capture_stats["lastDataAtMs"] = signals[-1].received_at_ms
        result_reasons = list(
            dict.fromkeys((*result.batch.invalid_reasons, *result.algorithm_result.invalid_reasons))
        )
        # Raw-only subscribers are governed by parsing validity. Algorithm
        # subscribers must also receive an explicit invalid event when an
        # evaluation times out/fails even though it produced no metrics.
        event_valid = result.valid
        event_reasons = result_reasons
        self.status["persistenceGuaranteed"] = result.persistence_guaranteed
        if self.recording_repository is not None:
            self.status["storageState"] = self.recording_repository.storage_status().state.value
        self._capture_stats["windows"] = int(self._capture_stats["windows"] or 0) + 1
        if not event_valid:
            self._capture_stats["invalidWindows"] = int(self._capture_stats["invalidWindows"] or 0) + 1
        if self.window_observer:
            try:
                await self.window_observer(result.batch, algorithm_payload, event_reasons, event_valid)
            except Exception:
                self.log.exception("Capture window observer failed")
        for session in tuple(self.sessions):
            for subscription in tuple(session.subscriptions.values()):
                payload = self.filtered_payload(raw, algorithm_payload, subscription.streams)
                algorithm_requested = bool(subscription.streams & {"eeg", "hr"})
                subscription_valid = result.batch.valid and (
                    result.algorithm_result.valid if algorithm_requested else True
                )
                subscription_reasons = (
                    result_reasons if algorithm_requested else list(result.batch.invalid_reasons)
                )
                if algorithm_requested and not algorithm_payload and not result.algorithm_result.valid:
                    payload["algorithm"] = {}
                if not payload or (not subscription_valid and not subscription.include_invalid):
                    continue
                if not subscription_valid:
                    payload["invalidReasons"] = subscription_reasons
                self._queue_live_message(
                    session,
                    subscription,
                    self.wire.envelope(
                        200,
                        self.event_data(
                            "data",
                            subscription.id,
                            result.batch.window_end_ms,
                            result.mode,
                            subscription_valid,
                            payload,
                        ),
                    ),
                )
        await asyncio.sleep(0)

    @property
    def live(self) -> bool:
        return self._connection_state_is_live(self.status["connectionState"])

    def _connection_state_is_live(self, state: object) -> bool:
        return state in self.live_connection_states

    @property
    def replay_available(self) -> bool:
        # The confirmed Kylin earphone transport is live USB serial only.
        # Historical replay remains available to the legacy Bluetooth strategy,
        # but must never be selected as a fallback for serial data.
        return self.supports_replay and self.replay_recording_id is not None

    @property
    def replay_recording_id(self) -> str | None:
        if not self.supports_replay:
            return None
        if self.replay_reader is not None:
            return self._active_replay_recording_id or self._replay_selection[0]
        return self._active_replay_recording_id or self.store.replay_recording_id(self.config.recording.replay_recording_id)

    async def prepare_query(self) -> None:
        if self.replay_reader is not None and not self.live and self._replay_task is None:
            self._replay_selection = await self.replay_reader.inspect(self.config.recording.replay_recording_id)

    def mode(self) -> str:
        # ``mode`` is constrained by the released northbound contract to
        # ``live``/``replay``.  Serial has no replay mode, so an offline serial
        # gateway stays in its configured live mode and reports disconnected
        # separately through connectionState.
        if not self.supports_replay:
            return "live"
        return "live" if self.live else "replay"

    def _offline_data_error(self) -> ProtocolError:
        return self.wire.offline_data_error(self)

    async def update_connection_error(self, error: str) -> None:
        """Record the latest device-transport failure for operational diagnosis.

        Adapters retry internally, so surfacing the exception here must not
        terminate the gateway or alter the published northbound schema.
        """
        safe_error = safe_log_text(error)
        self.connection_error = safe_error
        self.log.warning("Device connection attempt failed: transport=%s reason=%s", self.config.data_source.type, safe_error)

    def _reset_capture_stats(self) -> None:
        self._capture_final_summary_logged = False
        self._capture_stats.update(
            eegPackets=0,
            hrPackets=0,
            eegBytes=0,
            hrBytes=0,
            invalidPacketLengths=0,
            windows=0,
            invalidWindows=0,
            firstPacketAtMs=None,
            lastDataAtMs=None,
            lastSummaryAtMs=0,
        )

    def _log_capture_summary(self, reason: str) -> None:
        stats = self._capture_stats
        first_packet = stats["firstPacketAtMs"]
        last_packet = stats["lastDataAtMs"]
        duration_ms = (
            int(last_packet) - int(first_packet)
            if first_packet is not None and last_packet is not None
            else None
        )
        self.log.info(
            "Capture summary: reason=%s recordingId=%s windows=%s invalidWindows=%s "
            "eegPackets=%s eegBytes=%s hrPackets=%s hrBytes=%s invalidPacketLengths=%s "
            "firstPacketAtMs=%s lastPacketAtMs=%s durationMs=%s algorithmState=%s "
            "clients=%s subscriptions=%s",
            reason,
            self.store.recording_id or self.store.last_recording_id,
            stats["windows"],
            stats["invalidWindows"],
            stats["eegPackets"],
            stats["eegBytes"],
            stats["hrPackets"],
            stats["hrBytes"],
            stats["invalidPacketLengths"],
            first_packet,
            last_packet,
            duration_ms,
            self.status.get("algorithmState"),
            len(self.sessions),
            sum(len(session.subscriptions) for session in self.sessions),
        )

    def filtered_payload(self, raw, algorithm_payload, streams):
        return self.wire.filtered_payload(raw, algorithm_payload, streams)

    def event_data(self, event, subscription_id, timestamp_ms, mode, valid, payload):
        return self.wire.event_data(self, event, subscription_id, timestamp_ms, mode, valid, payload)

    def status_result(self):
        return self.wire.status_result(self)

    def _northbound_status(self):
        return self.wire.northbound_status(self)

    def available_streams(self) -> set[str]:
        available = {"status"}
        if self.live or self.replay_available:
            available.update({"eeg.raw", "hr.raw"})
        if self.algorithm.available or self.status.get("algorithmState") == "ready":
            available.update({"eeg", "hr"})
        recording_id = self.replay_recording_id
        if not self.live and recording_id:
            if self.replay_reader is not None:
                return available | set(self._replay_selection[1])
            for event in self.store.events(recording_id):
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
                    payload = {"status": self._northbound_status()}
                    self._queue_live_message(
                        session,
                        subscription,
                        self.wire.envelope(200, self.event_data("status", subscription.id, now_ms(), self.mode(), True, payload)),
                    )
        await asyncio.sleep(0)

    def error(self, request_id, error):
        return self.wire.error(request_id, error)

    def get_latest(self, session: ClientSession, params: dict, *, start_replay: bool = True) -> dict:
        streams = params.get("streams", ["eeg", "hr"])
        self.validate_streams(streams, allowed={"eeg", "hr"})
        if not self.live and not self.replay_available:
            raise self._offline_data_error()
        unavailable = set(streams) - self.available_streams()
        if unavailable:
            raise ProtocolError(409, STREAM_NOT_AVAILABLE_REASON, "One or more streams are unavailable.", details={"streams": sorted(unavailable)})
        algorithm, timestamp = self.latest_algorithm, self.latest_algorithm_timestamp
        latest_valid = algorithm is not None and timestamp is not None
        if self.live:
            snapshot = self.latest_snapshot.get(frozenset(streams)).result
            if snapshot is not None:
                algorithm = dict(snapshot.algorithm_result.metrics) or None
                timestamp = snapshot.batch.window_end_ms
                latest_valid = snapshot.valid and algorithm is not None
            if self._last_live_update is not None and (time.monotonic() - self._last_live_update) * 1000 >= self.config.data_source.stale_after_ms:
                latest_valid = False
        if not self.live and self.replay_available:
            if start_replay:
                self._start_replay_if_needed()
            algorithm, timestamp = self.latest_replay_algorithm()
            latest_valid = algorithm is not None and timestamp is not None
        if not algorithm or timestamp is None:
            return {"mode": self.mode(), "timestampMs": now_ms(), "valid": False, "payload": {}}
        return {"mode": self.mode(), "timestampMs": timestamp, "valid": latest_valid, "payload": self.filtered_payload({}, algorithm, frozenset(streams))}

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
        recording_id = self.replay_recording_id
        if not recording_id:
            return None, None
        if self.replay_reader is not None:
            return self._replay_selection[2], self._replay_selection[3]
        for item in reversed(self.store.events(recording_id)):
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
        data_streams = set(streams) - {"status"}
        include_invalid = params.get("includeInvalid", False)
        if not isinstance(include_invalid, bool):
            raise ProtocolError(400, "INVALID_REQUEST", "params.includeInvalid must be boolean.")
        if len(session.subscriptions) >= 4:
            raise ProtocolError(429, "RATE_LIMITED", "Subscription limit exceeded.", True)
        already_subscribed = set().union(*(item.streams for item in session.subscriptions.values())) if session.subscriptions else set()
        duplicate_streams = set(streams) & already_subscribed
        if duplicate_streams:
            raise ProtocolError(429, "RATE_LIMITED", "A stream is already subscribed on this connection.", True, {"streams": sorted(duplicate_streams)})
        if data_streams and not self.live and not self.replay_available:
            raise self._offline_data_error()
        unavailable = set(streams) - self.available_streams()
        if unavailable:
            raise ProtocolError(409, STREAM_NOT_AVAILABLE_REASON, "One or more streams are unavailable.", details={"streams": sorted(unavailable)})
        subscription = Subscription(f"sub-{uuid.uuid4().hex}", frozenset(streams), include_invalid, send)
        self.sessions.add(session)
        session.subscriptions[subscription.id] = subscription
        self.fanout.subscribe(subscription.id, subscription.streams)
        subscription.replay_delivery_task = asyncio.create_task(self._deliver_replay(session, subscription))
        subscription.live_delivery_task = asyncio.create_task(self._deliver_live(session, subscription))
        self.log.info("Subscription created: subscriptionId=%s streams=%s includeInvalid=%s mode=%s", subscription.id, ",".join(streams), include_invalid, self.mode())
        if start_replay and data_streams and not self.live:
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
        self.log.info("Subscription removed: subscriptionId=%s", subscription_id)
        return {"subscriptionId": subscription_id}

    def _start_replay_if_needed(self) -> None:
        """Start one replay clock for the gateway after the first B-side data request."""
        if not self.supports_replay:
            return
        if not self.sessions or self.live or (self._replay_task and not self._replay_task.done()):
            return
        recording_id = self.replay_recording_id
        if not recording_id:
            return
        self._reset_replay_progress()
        self._active_replay_recording_id = recording_id
        self._replay_task = asyncio.create_task(self._replay())
        self.log.info("Replay started: recordingId=%s", recording_id)

    async def _stop_replay(self) -> None:
        task, self._replay_task = self._replay_task, None
        self._reset_replay_progress()
        self._active_replay_recording_id = None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            self.log.info("Replay stopped")

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
            self.log.warning("Replay subscriber backlog exceeded limit; dropping subscriptionId=%s", subscription.id)
            if subscription.replay_delivery_task:
                subscription.replay_delivery_task.cancel()

    def _queue_live_message(self, session: ClientSession, subscription: Subscription, message: dict) -> None:
        if session.subscriptions.get(subscription.id) is not subscription:
            return
        if subscription.live_delivery_task is None or subscription.live_delivery_task.done():
            subscription.live_delivery_task = asyncio.create_task(self._deliver_live(session, subscription))
        payload = message["data"]["payload"]
        streams = frozenset(stream for stream in subscription.streams if (
            (stream == "status" and "status" in payload)
            or (stream == "eeg.raw" and "eegRaw" in payload)
            or (stream == "hr.raw" and "hrRaw" in payload)
            or (stream in {"eeg", "hr"} and "algorithm" in payload)
        ))
        overwritten = self.fanout.offer(subscription.id, streams, message)
        self.snapshot_overwrite_count += overwritten
        if overwritten:
            self.log.warning("Live subscriber latest value overwritten: subscriptionId=%s snapshotOverwriteCount=%s", subscription.id, self.snapshot_overwrite_count)

    async def _deliver_live(self, session: ClientSession, subscription: Subscription) -> None:
        try:
            while True:
                pending = await self.fanout.next(subscription.id)
                grouped = {}
                for stream, message in pending.items():
                    grouped.setdefault(id(message), (message, set()))[1].add(stream)
                for message, streams in grouped.values():
                    payload = message["data"]["payload"]
                    filtered = self.filtered_payload(payload, payload.get("algorithm"), frozenset(streams))
                    if "status" in streams:
                        filtered["status"] = payload["status"]
                    if "algorithm" in payload and streams & {"eeg", "hr"}:
                        filtered.setdefault("algorithm", {})
                    if "invalidReasons" in payload:
                        filtered["invalidReasons"] = payload["invalidReasons"]
                    await self._send_subscription(subscription, {**message, "data": {**message["data"], "payload": filtered}}, streams=streams)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log.warning("Live subscriber delivery failed; dropping subscriptionId=%s", subscription.id, exc_info=True)
        finally:
            self._drop_subscription(session, subscription)

    async def _send_subscription(self, subscription: Subscription, message: dict, *, streams=None) -> None:
        started = time.monotonic()
        streams = (subscription.streams - {"status"}) if streams is None else streams
        counters = [self.stream_metrics[stream] for stream in streams if stream in self.stream_metrics]
        try:
            async with asyncio.timeout(self.config.pipeline.send_timeout_ms / 1000):
                await subscription.send(message)
            self.delivery_metrics["sent"] += 1
            self.delivery_metrics["lastSuccessfulAtMs"] = now_ms()
            for values in counters:
                values["sent"] += 1
                values["lastSuccessfulAtMs"] = self.delivery_metrics["lastSuccessfulAtMs"]
        except Exception:
            self.delivery_metrics["failed"] += 1
            for values in counters:
                values["failed"] += 1
            raise
        finally:
            elapsed = int((time.monotonic() - started) * 1000)
            self.delivery_metrics["sendDurationMs"] += elapsed
            for values in counters:
                values["sendDurationMs"] += elapsed

    async def _deliver_replay(self, session: ClientSession, subscription: Subscription) -> None:
        try:
            while True:
                message = await subscription.replay_outbox.get()
                await self._send_subscription(subscription, message)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log.warning("Replay subscriber delivery failed; dropping subscriptionId=%s", subscription.id, exc_info=True)
        finally:
            self._drop_subscription(session, subscription)

    def _drop_subscription(self, session: ClientSession, subscription: Subscription) -> None:
        self.fanout.unsubscribe(subscription.id)
        session.subscriptions.pop(subscription.id, None)
        for task in (subscription.live_delivery_task, subscription.replay_delivery_task):
            if task and task is not asyncio.current_task() and not task.done():
                task.cancel()

    async def _remove_subscription(self, session: ClientSession, subscription: Subscription) -> None:
        self.fanout.unsubscribe(subscription.id)
        if session.subscriptions.get(subscription.id) is subscription:
            session.subscriptions.pop(subscription.id, None)
        for task in (subscription.replay_delivery_task, subscription.live_delivery_task):
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def _replay(self) -> None:
        recording_id = self._active_replay_recording_id
        if not recording_id:
            return
        try:
            cycle = 0
            while self._replay_should_continue():
                cycle += 1
                previous: int | None = None
                self._reset_replay_progress()
                async for item in self._replay_events(recording_id):
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
                        algorithm_requested = bool(subscription.streams & {"eeg", "hr"})
                        valid = item["valid"] if algorithm_requested else item.get("rawValid", item["valid"])
                        reasons = item["invalidReasons"] if algorithm_requested else item.get("rawInvalidReasons", item["invalidReasons"])
                        if algorithm_requested and not valid and not item["payload"].get("algorithm"):
                            payload["algorithm"] = {}
                        if not payload or (not valid and not subscription.include_invalid):
                            continue
                        if not valid:
                            payload["invalidReasons"] = reasons
                        self._queue_replay_message(session, subscription, self.wire.envelope(200, self.event_data("data", subscription.id, item["timestampMs"], "replay", valid, payload)))
                if not self._replay_should_continue():
                    return
                if previous is None:
                    self.log.warning("Replay recording contains no events: recordingId=%s", recording_id)
                    return
                ended = {"event": "replayEnded", "gatewayBootId": self.boot_id, "subjectId": self.config.recording.subject_id, "mode": "replay", "timestampMs": previous or now_ms(), "valid": True, "payload": {}, "recordingId": recording_id, "endedAtMs": now_ms()}
                for session, subscription in self._subscription_entries():
                    self._queue_replay_message(session, subscription, self.wire.envelope(200, ended))
                self.log.info("Replay cycle ended; restarting: recordingId=%s cycle=%s", recording_id, cycle)
                await asyncio.sleep(REPLAY_CYCLE_MIN_PAUSE_SECONDS)
        except asyncio.CancelledError:
            return
        except Exception:
            self.log.exception("Replay read failed: recordingId=%s", recording_id)
        finally:
            if self._replay_task is asyncio.current_task():
                self._replay_task = None
            self._reset_replay_progress()
            self._active_replay_recording_id = None

    async def _replay_events(self, recording_id):
        if self.replay_reader is not None:
            async for event in self.replay_reader.events(recording_id):
                yield {"timestampMs": event.timestamp_ms, "payload": dict(event.payload),
                       "valid": event.valid, "invalidReasons": list(event.invalid_reasons),
                       "rawValid": event.raw_valid if event.raw_valid is not None else event.valid,
                       "rawInvalidReasons": list(event.raw_invalid_reasons) if event.raw_valid is not None else list(event.invalid_reasons)}
        else:
            for event in self.store.events(recording_id):
                yield event

    async def close_session(self, session: ClientSession) -> None:
        for subscription in tuple(session.subscriptions.values()):
            await self._remove_subscription(session, subscription)
        self.sessions.discard(session)
        if not self.sessions:
            await self._stop_replay()
