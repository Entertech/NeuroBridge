#!/usr/bin/env python3
"""Run a locked bridge against a completed recording without exposing raw bytes.

The report contains counts, packet-length validation, bridge health, and output
field names only.  It never writes Base64 input or algorithm metric values.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import select
import subprocess
import sys
from typing import Any


PACKET_BYTES = {"eeg": 20, "hr": 1}
ALGORITHM_INPUT_STREAMS = ("eeg", "hr")


def load_rows(path: Path, stream: str, windows: dict[int, dict[str, list[dict[str, Any]]]], errors: Counter[str]) -> int:
    if not path.is_file():
        errors[f"{stream.upper()}_FILE_MISSING"] += 1
        return 0
    count = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
            timestamp = int(row["windowEndMs"])
            sequence = int(row["sequence"])
            raw = base64.b64decode(row["bytesBase64"], validate=True)
            if int(row["packetBytes"]) != PACKET_BYTES[stream] or len(raw) != PACKET_BYTES[stream]:
                raise ValueError("packet length differs from confirmed profile")
            if not bool(row.get("valid", False)):
                raise ValueError("recorded packet is marked invalid")
            row["_raw"] = raw
            row["_sequence"] = sequence
            windows[timestamp][stream].append(row)
            count += 1
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            errors[f"{stream.upper()}_ROW_INVALID"] += 1
            errors[f"{stream.upper()}_ROW_{line_number}_{type(error).__name__.upper()}"] += 1
    return count


def invoke_bridge(command: list[str], windows: dict[int, dict[str, list[dict[str, Any]]]], min_windows: int, timeout_seconds: float) -> dict[str, Any]:
    errors: Counter[str] = Counter()
    output_fields: Counter[str] = Counter()
    submitted = 0
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    except OSError as error:
        return {"outcome": "failed", "errors": {"BRIDGE_START_FAILED": str(error)}, "windowsSubmitted": 0, "outputFields": {}}
    assert process.stdin and process.stdout
    try:
        for timestamp in sorted(windows):
            streams = windows[timestamp]
            eeg = b"".join(row["_raw"] for row in sorted(streams.get("eeg", []), key=lambda row: row["_sequence"]))
            hr = b"".join(row["_raw"] for row in sorted(streams.get("hr", []), key=lambda row: row["_sequence"]))
            if not eeg and not hr:
                continue
            request = {"timestampMs": timestamp, "eegRawBase64": base64.b64encode(eeg).decode("ascii"), "hrRawBase64": base64.b64encode(hr).decode("ascii")}
            process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            process.stdin.flush()
            ready, _, _ = select.select([process.stdout], [], [], timeout_seconds)
            if not ready:
                errors["BRIDGE_RESPONSE_TIMEOUT"] += 1
                break
            try:
                response = json.loads(process.stdout.readline())
                if not isinstance(response, dict) or not isinstance(response.get("algorithm"), dict):
                    raise ValueError("algorithm object missing")
                if response.get("bridgeError") or response.get("pocError"):
                    raise ValueError("bridge reported an error")
                output_fields.update(str(key) for key in response["algorithm"])
                submitted += 1
            except (ValueError, json.JSONDecodeError):
                errors["BRIDGE_RESPONSE_INVALID"] += 1
    finally:
        process.stdin.close()
        process.wait(timeout=timeout_seconds)
    if process.returncode not in (0, None):
        errors["BRIDGE_EXIT_NONZERO"] += 1
    if submitted < min_windows:
        errors["INSUFFICIENT_VALID_WINDOWS"] += 1
    return {"outcome": "bridge_transport_passed" if not errors else "failed", "errors": dict(sorted(errors.items())), "windowsSubmitted": submitted, "outputFields": dict(sorted(output_fields.items()))}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Linux algorithm bridge transport with a completed, consented recording.")
    parser.add_argument("--recording-dir", type=Path, required=True)
    parser.add_argument("--recording-id", required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True, help="Output JSON summary; must be outside the source checkout.")
    parser.add_argument("--min-windows", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.min_windows <= 0 or args.timeout_seconds <= 0:
        parser.error("--min-windows and --timeout-seconds must be positive")
    session = args.recording_dir / "sessions" / args.recording_id
    if not session.is_dir() or not (session / "manifest.json").is_file():
        parser.error("recording must be a completed session directory with manifest.json")
    if not args.bridge.is_file() or not args.bridge.stat().st_mode & 0o111:
        parser.error("--bridge must be an executable file")
    windows: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    errors: Counter[str] = Counter()
    packet_counts = {stream: load_rows(session / "raw" / f"{stream}.jsonl", stream, windows, errors) for stream in PACKET_BYTES}
    result = invoke_bridge([str(args.bridge)], windows, args.min_windows, args.timeout_seconds) if not errors else {"outcome": "failed", "errors": {}, "windowsSubmitted": 0, "outputFields": {}}
    merged_errors = Counter(result["errors"])
    merged_errors.update(errors)
    summary = {
        "recordingId": args.recording_id,
        "bridgeSha256": sha256(args.bridge.read_bytes()).hexdigest(),
        "packetCounts": packet_counts,
        "windowsAvailable": len(windows),
        "windowsSubmitted": result["windowsSubmitted"],
        "outputFields": result["outputFields"],
        "outcome": "bridge_transport_passed" if result["outcome"] == "bridge_transport_passed" and not merged_errors else "failed",
        "errors": dict(sorted(merged_errors.items())),
        "notice": "This validates byte-preserving bridge transport only. It is not an algorithm accuracy or field-semantics acceptance result.",
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.summary.chmod(0o640)
    print(f"POC summary written: {args.summary}")
    return 0 if summary["outcome"] == "bridge_transport_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
