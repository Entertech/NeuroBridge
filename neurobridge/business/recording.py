from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import json
import uuid


class RecordingStore:
    """JSONL storage keeps raw BLE windows and algorithm outputs physically separate."""
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        (root / "raw").mkdir(exist_ok=True)
        (root / "algorithm").mkdir(exist_ok=True)
        self.recording_id: str | None = None

    def start(self) -> str:
        self.recording_id = f"rec-{uuid.uuid4()}"
        return self.recording_id

    def stop(self) -> None:
        self.recording_id = None

    def _append(self, category: str, row: dict) -> None:
        if not self.recording_id:
            return
        row = {"recordingId": self.recording_id, **row}
        with (self.root / category / f"{self.recording_id}.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")

    def save_raw(self, *, timestamp_ms: int, valid: bool, invalid_reasons: list[str], payload: dict) -> None:
        self._append("raw", {"timestampMs": timestamp_ms, "valid": valid, "invalidReasons": invalid_reasons, "payload": payload})

    def save_algorithm(self, *, timestamp_ms: int, valid: bool, invalid_reasons: list[str], algorithm: dict) -> None:
        self._append("algorithm", {"timestampMs": timestamp_ms, "valid": valid, "invalidReasons": invalid_reasons, "algorithm": algorithm})

    def has_recording(self, recording_id: str | None) -> bool:
        return bool(recording_id) and ((self.root / "raw" / f"{recording_id}.jsonl").exists() or (self.root / "algorithm" / f"{recording_id}.jsonl").exists())

    def events(self, recording_id: str) -> list[dict]:
        merged: dict[int, dict] = defaultdict(lambda: {"payload": {}, "valid": True, "invalidReasons": []})
        for category in ("raw", "algorithm"):
            path = self.root / category / f"{recording_id}.jsonl"
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                item = merged[int(row["timestampMs"])]
                item["timestampMs"] = int(row["timestampMs"])
                item["valid"] = item["valid"] and bool(row["valid"])
                item["invalidReasons"] = sorted(set(item["invalidReasons"] + row.get("invalidReasons", [])))
                if category == "raw":
                    item["payload"].update(row["payload"])
                else:
                    item["payload"]["algorithm"] = row["algorithm"]
        return [merged[key] for key in sorted(merged)]
