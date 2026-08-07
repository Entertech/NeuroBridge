#!/usr/bin/env python3
"""Summarize a NeuroBridge raw recording without printing biometric payload bytes."""

from __future__ import annotations

import argparse
import base64
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


STREAMS = {
    "eegRaw": ("FF31", 20),
    "hrRaw": ("FF51", 1),
}


@dataclass
class StreamStats:
    name: str
    expected_bytes: int
    windows: int = 0
    packets: int = 0
    declared_bytes: int = 0
    decoded_bytes: int = 0
    errors: Counter[str] = field(default_factory=Counter)

    def add(self, payload: Any) -> None:
        self.windows += 1
        if not isinstance(payload, dict):
            self.errors["PAYLOAD_NOT_OBJECT"] += 1
            return
        packet_bytes = payload.get("packetBytes")
        packet_count = payload.get("packetCount")
        byte_length = payload.get("byteLength")
        encoded = payload.get("bytesBase64")
        if packet_bytes != self.expected_bytes:
            self.errors["DECLARED_PACKET_BYTES_INVALID"] += 1
        if not isinstance(packet_count, int) or packet_count < 0:
            self.errors["PACKET_COUNT_INVALID"] += 1
            return
        if not isinstance(byte_length, int) or byte_length < 0:
            self.errors["BYTE_LENGTH_INVALID"] += 1
            return
        self.packets += packet_count
        self.declared_bytes += byte_length
        if byte_length != packet_count * self.expected_bytes:
            self.errors["PACKET_COUNT_AND_BYTE_LENGTH_MISMATCH"] += 1
        if not isinstance(encoded, str):
            self.errors["BASE64_MISSING"] += 1
            return
        try:
            decoded_length = len(base64.b64decode(encoded, validate=True))
        except (ValueError, TypeError):
            self.errors["BASE64_INVALID"] += 1
            return
        self.decoded_bytes += decoded_length
        if decoded_length != byte_length:
            self.errors["DECLARED_AND_DECODED_BYTE_LENGTH_MISMATCH"] += 1


def recording_path(root: Path, recording_id: str | None) -> Path:
    raw_dir = root / "raw"
    if recording_id:
        path = raw_dir / f"{recording_id}.jsonl"
        if not path.is_file():
            raise ValueError(f"Raw recording not found: {path}")
        return path
    candidates = sorted(raw_dir.glob("rec-*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise ValueError(f"No raw recordings found in: {raw_dir}")
    return candidates[0]


def report(path: Path) -> int:
    stats = {field: StreamStats(label, expected) for field, (label, expected) in STREAMS.items()}
    invalid_reasons: Counter[str] = Counter()
    timestamps: list[int] = []
    rows = 0

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        rows += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            invalid_reasons[f"JSON_LINE_{line_number}_INVALID"] += 1
            continue
        timestamp = row.get("timestampMs")
        if isinstance(timestamp, int):
            timestamps.append(timestamp)
        else:
            invalid_reasons["TIMESTAMP_INVALID"] += 1
        if not row.get("valid", False):
            reasons = row.get("invalidReasons", [])
            if isinstance(reasons, list):
                invalid_reasons.update(str(reason) for reason in reasons)
            else:
                invalid_reasons["INVALID_REASONS_INVALID"] += 1
        payload = row.get("payload", {})
        if not isinstance(payload, dict):
            invalid_reasons["ROW_PAYLOAD_INVALID"] += 1
            continue
        for field, stream in stats.items():
            if field in payload:
                stream.add(payload[field])

    print(f"recording_file: {path}")
    print(f"windows: {rows}")
    if timestamps:
        print(f"time_range_ms: {min(timestamps)}..{max(timestamps)}")
        if len(timestamps) > 1:
            deltas = [right - left for left, right in zip(timestamps, timestamps[1:])]
            print(f"window_interval_ms: min={min(deltas)} max={max(deltas)} distinct={','.join(map(str, sorted(set(deltas))))}")
    else:
        print("time_range_ms: unavailable")
    for field, stream in stats.items():
        print(f"{stream.name}: windows={stream.windows} packets={stream.packets} declared_bytes={stream.declared_bytes} decoded_bytes={stream.decoded_bytes}")
        for reason, count in sorted(stream.errors.items()):
            print(f"  {reason}: {count}")
    for reason, count in sorted(invalid_reasons.items()):
        print(f"invalid_or_parse_{reason}: {count}")
    return 0 if all(not stream.errors for stream in stats.values()) and not invalid_reasons else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a macOS headband raw recording without displaying raw bytes.")
    parser.add_argument("--recording-dir", type=Path, required=True, help="Recording root containing raw/ and algorithm/ directories.")
    parser.add_argument("--recording-id", help="Recording ID (for example rec-...). Defaults to the newest raw recording.")
    args = parser.parse_args()
    try:
        return report(recording_path(args.recording_dir, args.recording_id))
    except (OSError, ValueError) as error:
        parser.error(str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
