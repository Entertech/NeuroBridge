#!/usr/bin/env python3
"""POC bridge that verifies raw-window delivery without inventing algorithm results.

The production C++ SDK must replace this program only after its expected packet
grouping has been validated against actual Flowtime recordings.  The audit log
contains only timestamp and byte counts; it intentionally never stores payloads.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any


def byte_length(value: Any) -> int:
    if not isinstance(value, str):
        raise ValueError("raw value is not base64 text")
    return len(base64.b64decode(value, validate=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Accept raw gateway windows and write a non-sensitive audit summary.")
    parser.add_argument("--audit-file", type=Path, required=True)
    args = parser.parse_args()
    args.audit_file.parent.mkdir(parents=True, exist_ok=True)

    with args.audit_file.open("a", encoding="utf-8") as audit:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                timestamp = request.get("timestampMs")
                if not isinstance(timestamp, int):
                    raise ValueError("timestampMs is invalid")
                row = {
                    "timestampMs": timestamp,
                    "receivedAtMs": int(time.time() * 1000),
                    "eegByteLength": byte_length(request.get("eegRawBase64", "")),
                    "hrRawByteLength": byte_length(request.get("hrRawBase64", "")),
                }
                audit.write(json.dumps(row, separators=(",", ":")) + "\n")
                audit.flush()
                # An empty object means bytes were handed to the bridge successfully.
                # It deliberately avoids fabricating attention, heart-rate or band-power values.
                print('{"algorithm":{}}', flush=True)
            except (ValueError, TypeError, json.JSONDecodeError, UnicodeError) as error:
                print(json.dumps({"algorithm": {}, "pocError": str(error)}, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
