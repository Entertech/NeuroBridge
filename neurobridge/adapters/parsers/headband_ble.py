"""Pure BLE notification parser for the confirmed headband profile."""

from __future__ import annotations

import uuid

from ...domain.raw import DeviceFrame, ParseDiagnostic, ParseOutcome, RawChunk
from ...domain.signal import ParsedSignal
from ...ports.raw_parser import FlushReason


class HeadbandBleParser:
    _CHANNELS = {"ff31": ("eeg", 20), "ff51": ("hr", 1), "ff32": ("device_status", None)}

    def feed(self, chunk: RawChunk) -> ParseOutcome:
        definition = self._CHANNELS.get(chunk.channel.lower())
        if definition is None:
            diagnostic = ParseDiagnostic("unsupported_channel", "warning", len(chunk.data), chunk.received_at_ms, details={"channel": chunk.channel})
            return ParseOutcome(diagnostics=(diagnostic,), discarded_bytes=len(chunk.data))
        signal_type, expected = definition
        valid = expected is None or len(chunk.data) == expected
        reasons = () if valid else (f"{signal_type.upper()}_PACKET_LENGTH_INVALID",)
        frame_id = f"frame-{uuid.uuid4().hex}"
        frame = DeviceFrame(
            "headband_ble",
            chunk.data,
            frame_id,
            None,
            chunk.received_at_ms,
            (chunk.trace_id,),
            chunk.connection_session_id,
        )
        signal = ParsedSignal(
            signal_type,
            chunk.data,
            "bytes",
            None,
            {"channel": chunk.channel.lower(), "expected_bytes": expected},
            (frame_id,),
            chunk.received_at_ms,
            valid,
            reasons,
        )
        diagnostics = () if valid else (
            ParseDiagnostic("invalid_length", "warning", len(chunk.data), chunk.received_at_ms, details={"expected": expected, "channel": chunk.channel.lower()}),
        )
        return ParseOutcome((frame,), (signal,), diagnostics)

    def flush(self, reason: FlushReason) -> ParseOutcome:
        return ParseOutcome()

    def reset(self) -> None:
        return None
