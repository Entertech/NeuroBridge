"""Explicit mapping from domain values to the locked wire envelope."""

from __future__ import annotations

import base64
from dataclasses import replace

from ...domain.result import WindowResult
from ...versioning import NORTHBOUND_PROTOCOL_VERSION


def envelope(code: int, data: dict[str, object], message: str = "OK") -> dict[str, object]:
    return {"protocolVersion": NORTHBOUND_PROTOCOL_VERSION, "code": code, "data": data, "message": message}


def window_result_data(result: WindowResult, *, gateway_boot_id: str, subject_id: str | None) -> dict[str, object]:
    payload, _, _ = project_window(result)
    payload["algorithm"] = dict(result.algorithm_result.metrics)
    return {
        "gatewayBootId": gateway_boot_id,
        "mode": result.mode,
        "subjectId": subject_id,
        "timestampMs": result.batch.window_end_ms,
        "valid": result.valid,
        "payload": payload,
    }


def project_window(result: WindowResult):
    """Serialize every signal in a window, preserving batching and wire fields."""
    raw, refs = {}, {"eeg": None, "hr": None}
    grouped = {name: [s for s in result.batch.signals if s.signal_type == name and isinstance(s.samples, bytes)] for name in refs}
    if result.batch.device_protocol == "headset_rev181":
        # The public raw contract remains 20-byte sequence+EEG and 1-byte HR;
        # the domain deliberately stores sequence separately from 18-byte EEG.
        grouped["eeg"] = [replace(s, samples=int(s.window_hint["sequence"]).to_bytes(2, "big") + s.samples,
                                  sample_format="bytes") for s in grouped["eeg"]]
        grouped["hr"] = [replace(s, sample_format="bytes") for s in grouped["hr"]]
    for name, signals in grouped.items():
        if not signals:
            continue
        combined = b"".join(s.samples for s in signals)
        raw[f"{name}Raw"] = {
            "encoding": "base64", "sampleFormat": signals[0].sample_format,
            "packetBytes": len(signals[0].samples), "packetCount": len(signals),
            "byteLength": len(combined), "windowStartMs": result.batch.window_start_ms,
            "windowEndMs": result.batch.window_end_ms,
            "bytesBase64": base64.b64encode(combined).decode("ascii"),
        }
        refs[name] = {
            "receivedAtMsStart": signals[0].received_at_ms,
            "receivedAtMsEnd": signals[-1].received_at_ms, "packetCount": len(signals),
            "windowStartMs": result.batch.window_start_ms, "windowEndMs": result.batch.window_end_ms,
        }
    return raw, refs, grouped
