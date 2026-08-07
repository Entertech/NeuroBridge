from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "run-algorithm-poc.py"


def raw_row(recording_id: str, timestamp: int, sequence: int, size: int, value: bytes) -> dict:
    return {
        "sessionId": recording_id,
        "receivedAtMs": timestamp - 1,
        "windowStartMs": timestamp - 600,
        "windowEndMs": timestamp,
        "sequence": sequence,
        "packetBytes": size,
        "encoding": "base64",
        "bytesBase64": base64.b64encode(value).decode("ascii"),
        "valid": True,
        "invalidReasons": [],
    }


class AlgorithmPocToolTests(unittest.TestCase):
    def create_recording(self, root: Path, *, invalid_eeg: bool = False) -> str:
        recording_id = "rec-poc"
        raw = root / "sessions" / recording_id / "raw"
        raw.mkdir(parents=True)
        (raw.parent / "manifest.json").write_text("{}\n", encoding="utf-8")
        rows = {
            "eeg": [raw_row(recording_id, 1200, 1, 20, b"e" * (19 if invalid_eeg else 20))],
            "hr": [raw_row(recording_id, 1200, 1, 1, b"r")],
        }
        for stream, stream_rows in rows.items():
            (raw / f"{stream}.jsonl").write_text("\n".join(json.dumps(row) for row in stream_rows) + "\n", encoding="utf-8")
        return recording_id

    def create_bridge(self, root: Path) -> Path:
        bridge = root / "bridge.py"
        bridge.write_text(
            "#!/usr/bin/env python3\nimport json, sys\nfor _ in sys.stdin:\n print(json.dumps({'algorithm': {'attention': 1, 'hr': {'value': 2}}}), flush=True)\n",
            encoding="utf-8",
        )
        bridge.chmod(0o755)
        return bridge

    def run_tool(self, root: Path, recording_id: str, bridge: Path, summary: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(TOOL), "--recording-dir", str(root), "--recording-id", recording_id, "--bridge", str(bridge), "--summary", str(summary), "--min-windows", "1"],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_reports_bridge_transport_without_storing_values_or_raw_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "summary.json"
            result = self.run_tool(root, self.create_recording(root), self.create_bridge(root), summary)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(report["outcome"], "bridge_transport_passed")
            self.assertEqual(report["packetCounts"], {"eeg": 1, "hr": 1})
            self.assertEqual(report["outputFields"], {"attention": 1, "hr": 1})
            self.assertNotIn("bytesBase64", summary.read_text(encoding="utf-8"))
            self.assertNotIn("\"value\": 2", summary.read_text(encoding="utf-8"))

    def test_rejects_recordings_that_do_not_match_the_confirmed_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "summary.json"
            result = self.run_tool(root, self.create_recording(root, invalid_eeg=True), self.create_bridge(root), summary)
            self.assertEqual(result.returncode, 1)
            report = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(report["outcome"], "failed")
            self.assertIn("EEG_ROW_INVALID", report["errors"])
