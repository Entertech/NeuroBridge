"""Connection, data, and storage state machines."""

from __future__ import annotations

from dataclasses import replace

from ..domain.status import (
    ConnectionState,
    DataState,
    DataStatusSnapshot,
    DeviceConnectionEvent,
    StorageState,
)
from ..ports.recording import StorageStatus


_CONNECTION_TRANSITIONS: dict[ConnectionState, frozenset[ConnectionState]] = {
    ConnectionState.DISCONNECTED: frozenset({ConnectionState.DISCOVERING}),
    ConnectionState.DISCOVERING: frozenset({ConnectionState.CONNECTING, ConnectionState.RECONNECTING}),
    ConnectionState.CONNECTING: frozenset({ConnectionState.CONNECTED, ConnectionState.VALIDATING, ConnectionState.RECONNECTING}),
    ConnectionState.VALIDATING: frozenset({ConnectionState.CONNECTED, ConnectionState.VALIDATION_FAILED, ConnectionState.RECONNECTING}),
    ConnectionState.VALIDATION_FAILED: frozenset({ConnectionState.RECONNECTING}),
    ConnectionState.CONNECTED: frozenset({ConnectionState.RECONNECTING, ConnectionState.DISCONNECTED}),
    ConnectionState.RECONNECTING: frozenset({ConnectionState.DISCOVERING, ConnectionState.DISCONNECTED}),
}


class ConnectionStateMachine:
    def __init__(self) -> None:
        self.state = ConnectionState.DISCONNECTED
        self.connection_session_id: str | None = None

    def transition(
        self,
        state: ConnectionState,
        occurred_at_ms: int,
        *,
        connection_session_id: str | None = None,
        existing_stream: bool = False,
        reason: str | None = None,
        retryable: bool = True,
    ) -> DeviceConnectionEvent:
        if state == self.state:
            raise ValueError(f"Connection state is already {state}")
        if state not in _CONNECTION_TRANSITIONS[self.state]:
            raise ValueError(f"Illegal connection transition: {self.state} -> {state}")
        if state == ConnectionState.CONNECTED and not connection_session_id:
            raise ValueError("Connected state requires a connection_session_id")
        self.state = state
        if state == ConnectionState.CONNECTED:
            self.connection_session_id = connection_session_id
        elif state in {ConnectionState.DISCONNECTED, ConnectionState.RECONNECTING, ConnectionState.VALIDATION_FAILED}:
            self.connection_session_id = None
        return DeviceConnectionEvent(state, occurred_at_ms, connection_session_id, existing_stream, reason, retryable)


_DATA_TRANSITIONS: dict[DataState, frozenset[DataState]] = {
    DataState.UNAVAILABLE: frozenset({DataState.PREPARING}),
    DataState.PREPARING: frozenset({DataState.READY, DataState.ERROR, DataState.UNAVAILABLE}),
    DataState.READY: frozenset({DataState.STREAMING, DataState.ERROR, DataState.UNAVAILABLE}),
    DataState.STREAMING: frozenset({DataState.STALE, DataState.ERROR, DataState.UNAVAILABLE}),
    DataState.STALE: frozenset({DataState.STREAMING, DataState.ERROR, DataState.UNAVAILABLE}),
    DataState.ERROR: frozenset({DataState.PREPARING, DataState.UNAVAILABLE}),
}


class DataStateMachine:
    def __init__(self) -> None:
        self.snapshot = DataStatusSnapshot()

    def on_connection(self, event: DeviceConnectionEvent) -> DataStatusSnapshot:
        if event.state == ConnectionState.CONNECTED:
            return self.transition(DataState.PREPARING)
        if event.state in {
            ConnectionState.DISCONNECTED,
            ConnectionState.RECONNECTING,
            ConnectionState.VALIDATION_FAILED,
        } and self.snapshot.data_state != DataState.UNAVAILABLE:
            return self.transition(DataState.UNAVAILABLE)
        return self.snapshot

    def transition(self, state: DataState, **changes: object) -> DataStatusSnapshot:
        current = self.snapshot.data_state
        if state != current and state not in _DATA_TRANSITIONS[current]:
            raise ValueError(f"Illegal data transition: {current} -> {state}")
        self.snapshot = replace(self.snapshot, data_state=state, **changes)
        return self.snapshot

    def produced(self, timestamp_ms: int, *, valid: bool) -> DataStatusSnapshot:
        target = DataState.STREAMING
        if self.snapshot.data_state not in {DataState.READY, DataState.STREAMING, DataState.STALE}:
            raise ValueError("Data can only be produced after the pipeline is ready")
        counters = dict(self.snapshot.counters)
        key = "produced_windows" if valid else "invalid_windows"
        counters[key] = counters.get(key, 0) + 1
        return self.transition(
            target,
            last_produced_at_ms=timestamp_ms,
            recent_error=None,
            error_stage=None,
            counters=counters,
        )


class StorageHealthTracker:
    """Apply documented watermarks and three-check recovery debounce."""

    def __init__(
        self,
        warning_threshold_bytes: int = 5 * 1024**3,
        critical_threshold_bytes: int = 1 * 1024**3,
        recovery_margin_bytes: int = 1 * 1024**3,
        recovery_checks: int = 3,
    ) -> None:
        if critical_threshold_bytes >= warning_threshold_bytes:
            raise ValueError("critical storage threshold must be below warning threshold")
        self.warning = warning_threshold_bytes
        self.critical = critical_threshold_bytes
        self.recovery_margin = recovery_margin_bytes
        self.recovery_checks = recovery_checks
        self.state = StorageState.OK
        self._healthy_checks = 0
        self._write_succeeded_since_degraded = True

    def observe(self, available_bytes: int, *, write_succeeded: bool | None = None, failure_state: StorageState = StorageState.ERROR) -> StorageState:
        if write_succeeded is False:
            self.state = failure_state
            self._healthy_checks = 0
            self._write_succeeded_since_degraded = False
            return self.state
        if self.state == StorageState.OK:
            if available_bytes <= self.critical:
                self.state = StorageState.FULL
                self._write_succeeded_since_degraded = False
            elif available_bytes <= self.warning:
                self.state = StorageState.WARNING
                self._write_succeeded_since_degraded = False
            return self.state
        if write_succeeded is True:
            self._write_succeeded_since_degraded = True
        if available_bytes <= self.critical:
            self.state = StorageState.FULL
            self._healthy_checks = 0
        elif available_bytes <= self.warning:
            self.state = StorageState.WARNING
            self._healthy_checks = 0
        elif available_bytes >= self.warning + self.recovery_margin and self._write_succeeded_since_degraded:
            self._healthy_checks += 1
            if self._healthy_checks >= self.recovery_checks:
                self.state = StorageState.OK
                self._healthy_checks = 0
        else:
            self._healthy_checks = 0
        return self.state

    def status(self, available_bytes: int | None = None) -> StorageStatus:
        return StorageStatus(
            state=self.state,
            available_bytes=available_bytes,
            persistence_guaranteed=self.state == StorageState.OK,
            details={
                "healthyChecks": self._healthy_checks,
                "recoveryChecksRequired": self.recovery_checks,
                "writeSucceededSinceDegraded": self._write_succeeded_since_degraded,
            },
        )
