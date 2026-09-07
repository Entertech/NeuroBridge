"""Explicit mapping from domain values to the locked wire envelope."""

from __future__ import annotations

import base64

from ...domain.result import WindowResult
from ...versioning import NORTHBOUND_PROTOCOL_VERSION


def envelope(code: int, data: dict[str, object], message: str = "OK") -> dict[str, object]:
    return {"protocolVersion": NORTHBOUND_PROTOCOL_VERSION, "code": code, "data": data, "message": message}


def window_result_data(result: WindowResult, *, gateway_boot_id: str, subject_id: str | None) -> dict[str, object]:
    payload: dict[str, object] = {"algorithm": dict(result.algorithm_result.metrics)}
    for signal in result.batch.signals:
        if isinstance(signal.samples, bytes) and signal.signal_type in {"eeg", "hr"}:
            payload[f"{signal.signal_type}Raw"] = {
                "encoding": "base64",
                "sampleFormat": signal.sample_format,
                "byteLength": len(signal.samples),
                "bytesBase64": base64.b64encode(signal.samples).decode("ascii"),
            }
    return {
        "gatewayBootId": gateway_boot_id,
        "mode": result.mode,
        "subjectId": subject_id,
        "timestampMs": result.batch.window_end_ms,
        "valid": result.valid,
        "payload": payload,
    }
