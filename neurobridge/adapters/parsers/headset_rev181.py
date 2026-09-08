"""Pure parser for the confirmed revision-181 28-byte headset frame."""

from __future__ import annotations

import uuid
from collections import deque

from ...domain.raw import DeviceFrame, ParseDiagnostic, ParseOutcome, RawChunk
from ...domain.signal import ParsedSignal
from ...ports.raw_parser import FlushReason
from .sequence import SequenceLossTracker

FRAME_HEADER = b"\xAA\xAA\xAA"
FRAME_TAIL = b"\xBB\xBB\xBB"
FRAME_BYTES = 28
EEG_START = 6
EEG_END = 24
HR_OFFSET = 24
SEQUENCE_MODULUS = 1 << 16


class HeadsetRev181Parser:
    """Split arbitrary serial reads without performing I/O or SDK mapping."""

    def __init__(self, max_buffer_bytes: int = 65536) -> None:
        if max_buffer_bytes < FRAME_BYTES:
            raise ValueError("max_buffer_bytes must hold at least one frame")
        self.max_buffer_bytes = max_buffer_bytes
        self._buffer = bytearray()
        self._provenance: deque[tuple[str, int]] = deque()
        self._session_id: str | None = None
        self._loss = SequenceLossTracker()
        self._last_received_at_ms: int | None = None

    def feed(self, chunk: RawChunk) -> ParseOutcome:
        diagnostics: list[ParseDiagnostic] = []
        discarded = 0
        if self._session_id not in {None, chunk.connection_session_id}:
            if self._buffer:
                diagnostics.append(self._diagnostic("partial_frame", len(self._buffer), chunk.received_at_ms, reason="connection_changed"))
                discarded += len(self._buffer)
            self.reset()
        self._session_id = chunk.connection_session_id
        self._last_received_at_ms = chunk.received_at_ms
        self._buffer.extend(chunk.data)
        if chunk.data:
            self._provenance.append((chunk.trace_id, len(chunk.data)))
        if len(self._buffer) > self.max_buffer_bytes:
            count = len(self._buffer) - self.max_buffer_bytes
            self._consume(count)
            discarded += count
            diagnostics.append(self._diagnostic("buffer_overflow", count, chunk.received_at_ms))

        frames: list[DeviceFrame] = []
        signals: list[ParsedSignal] = []
        while self._buffer:
            offset = self._buffer.find(FRAME_HEADER)
            if offset < 0:
                keep = self._header_suffix_length()
                count = len(self._buffer) - keep
                if count:
                    self._consume(count)
                    discarded += count
                    diagnostics.append(self._diagnostic("noise", count, chunk.received_at_ms))
                break
            if offset:
                self._consume(offset)
                discarded += offset
                diagnostics.append(self._diagnostic("noise", offset, chunk.received_at_ms))
            if len(self._buffer) < 4:
                break
            if self._buffer[3] != FRAME_BYTES:
                observed = self._buffer[3]
                self._consume(1)
                discarded += 1
                diagnostics.append(self._diagnostic("invalid_length", 1, chunk.received_at_ms, observed=observed, expected=FRAME_BYTES))
                continue
            if len(self._buffer) < FRAME_BYTES:
                break
            raw = bytes(self._buffer[:FRAME_BYTES])
            if raw[-3:] != FRAME_TAIL:
                self._consume(1)
                discarded += 1
                diagnostics.append(self._diagnostic("invalid_tail", 1, chunk.received_at_ms))
                continue
            source_chunk_ids = self._consume(FRAME_BYTES)
            sequence = int.from_bytes(raw[4:6], "big")
            diagnostics.extend(self._sequence_diagnostics(sequence, chunk.received_at_ms))
            frame_id = f"frame-{uuid.uuid4().hex}"
            frame = DeviceFrame(
                "headset_rev181",
                raw,
                frame_id,
                sequence,
                chunk.received_at_ms,
                source_chunk_ids,
                chunk.connection_session_id,
            )
            frames.append(frame)
            hints = {"sequence": sequence}
            signals.extend(
                (
                    ParsedSignal("eeg", raw[EEG_START:EEG_END], "bytes", None, hints, (frame_id,), chunk.received_at_ms),
                    ParsedSignal("hr", raw[HR_OFFSET : HR_OFFSET + 1], "uint8", None, hints, (frame_id,), chunk.received_at_ms),
                )
            )
        return ParseOutcome(tuple(frames), tuple(signals), tuple(diagnostics), len(self._buffer), discarded)

    def flush(self, reason: FlushReason) -> ParseOutcome:
        if not self._buffer:
            self.reset()
            return ParseOutcome()
        count = len(self._buffer)
        diagnostic = self._diagnostic("partial_frame", count, self._last_received_at_ms or 0, reason=reason.value)
        self.reset()
        return ParseOutcome(diagnostics=(diagnostic,), discarded_bytes=count)

    def reset(self) -> None:
        self._buffer.clear()
        self._provenance.clear()
        self._session_id = None
        self._loss = SequenceLossTracker()
        self._last_received_at_ms = None

    def _header_suffix_length(self) -> int:
        for length in range(len(FRAME_HEADER) - 1, 0, -1):
            if self._buffer.endswith(FRAME_HEADER[:length]):
                return length
        return 0

    def _consume(self, count: int) -> tuple[str, ...]:
        del self._buffer[:count]
        ids = []
        while count and self._provenance:
            trace_id, size = self._provenance.popleft()
            ids.append(trace_id)
            consumed = min(count, size)
            count -= consumed
            if size > consumed:
                self._provenance.appendleft((trace_id, size - consumed))
        return tuple(dict.fromkeys(ids))

    def _sequence_diagnostics(self, sequence: int, timestamp_ms: int) -> list[ParseDiagnostic]:
        observation = self._loss.observe(sequence)
        if observation.classification in {"baseline", "in_order"}:
            return []
        return [self._diagnostic("sequence_" + observation.classification, 0, timestamp_ms,
                                expected=observation.expected_sequence, actual=sequence,
                                missing=observation.gap_packets)]

    @staticmethod
    def _diagnostic(kind: str, count: int, timestamp_ms: int, **details: object) -> ParseDiagnostic:
        return ParseDiagnostic(kind, "warning", count, timestamp_ms, details=details)
