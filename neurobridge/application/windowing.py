"""Common signal window assembly independent of BLE and serial framing."""

from __future__ import annotations

import uuid

from ..domain.signal import ParsedSignal, ParsedSignalBatch


class SignalWindowAssembler:
    def __init__(self, interval_ms: int = 600) -> None:
        if interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        self.interval_ms = interval_ms
        self._signals: list[ParsedSignal] = []
        self._window_start_ms: int | None = None
        self._connection_session_id: str | None = None
        self._device_protocol: str | None = None
        self._recording_session_id: str | None = None

    def append(
        self,
        signal: ParsedSignal,
        *,
        device_protocol: str,
        connection_session_id: str,
        recording_session_id: str,
    ) -> tuple[ParsedSignalBatch, ...]:
        if self._connection_session_id not in {None, connection_session_id}:
            raise ValueError("SignalWindowAssembler cannot span connection sessions")
        ready: list[ParsedSignalBatch] = []
        start = signal.received_at_ms - signal.received_at_ms % self.interval_ms
        if self._window_start_ms is not None and start != self._window_start_ms:
            batch = self._finish()
            if batch is not None:
                ready.append(batch)
        if self._window_start_ms is None:
            self._window_start_ms = start
            self._connection_session_id = connection_session_id
            self._device_protocol = device_protocol
            self._recording_session_id = recording_session_id
        self._signals.append(signal)
        return tuple(ready)

    def flush(self) -> ParsedSignalBatch | None:
        return self._finish()

    @property
    def window_end_ms(self) -> int | None:
        if self._window_start_ms is None:
            return None
        return self._window_start_ms + self.interval_ms

    def reset(self) -> None:
        self._signals.clear()
        self._window_start_ms = None
        self._connection_session_id = None
        self._device_protocol = None
        self._recording_session_id = None

    def _finish(self) -> ParsedSignalBatch | None:
        if self._window_start_ms is None or not self._signals:
            self.reset()
            return None
        reasons = tuple(dict.fromkeys(reason for signal in self._signals for reason in signal.invalid_reasons))
        frame_refs = tuple(dict.fromkeys(ref for signal in self._signals for ref in signal.frame_refs))
        batch = ParsedSignalBatch(
            batch_id=f"batch-{uuid.uuid4().hex}",
            device_protocol=self._device_protocol or "unknown",
            connection_session_id=self._connection_session_id or "unknown",
            recording_session_id=self._recording_session_id or "unknown",
            window_start_ms=self._window_start_ms,
            window_end_ms=self._window_start_ms + self.interval_ms,
            signals=tuple(self._signals),
            frame_refs=frame_refs,
            valid=not reasons,
            invalid_reasons=reasons,
        )
        self.reset()
        return batch
