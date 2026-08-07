from __future__ import annotations

import base64
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import time
import uuid
import zipfile


RAW_STREAMS = {"eeg": 20, "hr": 1}
# Kept only for reading sessions made by the superseded FF52 profile.  New
# sessions never create this file and it remains hidden from northbound replay.
LEGACY_REPLAY_STREAMS = {"hr_native": 16}
ALGORITHM_FILES = (
    "bio", "hr", "hrv", "attention", "flow", "pressure", "relaxation",
    "pleasure", "coherence", "arousal", "sleep",
)
ARCHIVE_FORMAT_VERSION = "1.0"
CAPTURE_PACKAGE_DOCUMENT_VERSION = "0.1"
CAPTURE_PACKAGE_DOCUMENT_FILENAME = "头环数据采集包格式说明_v0.1.pdf"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAPTURE_PACKAGE_MARKDOWN = PROJECT_ROOT / "doc/tech/对外/头环数据采集包格式说明/头环数据采集包格式说明_v0.1.md"
CAPTURE_PACKAGE_RENDERER = PROJECT_ROOT / "tools/render-protocol-pdf.sh"


def _deep_merge(target: dict, update: dict) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


class RecordingStore:
    """Persist raw packets and independent algorithm metric events per session."""

    def __init__(self, root: Path, capture_package_pdf: Path | None = None) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        # Retain these legacy directories so pre-v1.0 recordings remain replayable.
        (root / "raw").mkdir(exist_ok=True)
        (root / "algorithm").mkdir(exist_ok=True)
        (root / "sessions").mkdir(exist_ok=True)
        (root / "exports").mkdir(exist_ok=True)
        self.recording_id: str | None = None
        self.last_recording_id: str | None = None
        self._sequence: dict[str, int] = {}
        self._started_at_ms: int | None = None
        self._session_started_at_ms: dict[str, int] = {}
        self._capture_package_pdf = capture_package_pdf

    def start(self, started_at_ms: int | None = None) -> str:
        self.recording_id = f"rec-{uuid.uuid4()}"
        self.last_recording_id = self.recording_id
        self._sequence = {stream: 0 for stream in RAW_STREAMS}
        self._started_at_ms = started_at_ms if started_at_ms is not None else int(time.time() * 1000)
        self._session_started_at_ms[self.recording_id] = self._started_at_ms
        session = self._session_dir(self.recording_id)
        (session / "raw").mkdir(parents=True, exist_ok=True)
        (session / "algorithm").mkdir(exist_ok=True)
        for stream in RAW_STREAMS:
            (session / "raw" / f"{stream}.jsonl").touch()
        for metric in ALGORITHM_FILES:
            (session / "algorithm" / f"{metric}.jsonl").touch()
        self._write_manifest(self.recording_id)
        return self.recording_id

    def stop(self) -> None:
        if self.recording_id and self._session_dir(self.recording_id).is_dir():
            self._write_manifest(self.recording_id)
            self.last_recording_id = self.recording_id
        self.recording_id = None
        self._sequence = {}
        self._started_at_ms = None

    def _session_dir(self, recording_id: str) -> Path:
        return self.root / "sessions" / recording_id

    def _append_session(self, recording_id: str, relative_path: str, row: dict) -> None:
        path = self._session_dir(recording_id) / relative_path
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps({"sessionId": recording_id, **row}, separators=(",", ":"), ensure_ascii=False) + "\n")

    def save_raw_packet(self, *, stream: str, received_at_ms: int, window_start_ms: int, window_end_ms: int, value: bytes) -> None:
        if not self.recording_id or stream not in RAW_STREAMS:
            return
        self._sequence[stream] = self._sequence.get(stream, 0) + 1
        expected = RAW_STREAMS[stream]
        valid = len(value) == expected
        self._append_session(
            self.recording_id,
            f"raw/{stream}.jsonl",
            {
                "receivedAtMs": received_at_ms,
                "windowStartMs": window_start_ms,
                "windowEndMs": window_end_ms,
                "sequence": self._sequence[stream],
                "packetBytes": len(value),
                "encoding": "base64",
                "bytesBase64": base64.b64encode(value).decode("ascii"),
                "valid": valid,
                "invalidReasons": [] if valid else [f"{stream.upper()}_PACKET_LENGTH_INVALID"],
            },
        )

    def save_algorithm_events(self, *, algorithm: dict, computed_at_ms: int, eeg_source: dict | None, hr_source: dict | None, valid: bool, invalid_reasons: list[str]) -> None:
        if not self.recording_id:
            return

        def save(metric: str, value: object, fragment: dict, source: dict | None) -> None:
            if source is None:
                return
            self._append_session(
                self.recording_id or "",
                f"algorithm/{metric}.jsonl",
                {
                    "metric": metric,
                    "timestampMs": source["receivedAtMsEnd"],
                    "computedAtMs": computed_at_ms,
                    "source": source,
                    "value": value,
                    "algorithm": fragment,
                    "valid": valid,
                    "invalidReasons": invalid_reasons,
                },
            )

        eeg = algorithm.get("eeg")
        if isinstance(eeg, dict):
            save("bio", eeg, {"eeg": eeg}, eeg_source)
        for metric in ("attention", "relaxation", "pleasure"):
            if metric in algorithm:
                save(metric, algorithm[metric], {metric: algorithm[metric]}, eeg_source)
        flow = algorithm.get("flow")
        if isinstance(flow, dict):
            save("flow", flow, {"flow": flow}, eeg_source)
        sleep = algorithm.get("sleep")
        if isinstance(sleep, dict) and sleep.get("updated") is True:
            save("sleep", sleep, {"sleep": sleep}, eeg_source)

        hr = algorithm.get("hr")
        if isinstance(hr, dict):
            if "value" in hr:
                save("hr", hr["value"], {"hr": {"value": hr["value"]}}, hr_source)
            if "hrv" in hr:
                save("hrv", hr["hrv"], {"hr": {"hrv": hr["hrv"]}}, hr_source)
        for metric in ("pressure", "coherence", "arousal"):
            if metric in algorithm:
                save(metric, algorithm[metric], {metric: algorithm[metric]}, hr_source)

    @staticmethod
    def source_reference(packets: list, *, window_start_ms: int, window_end_ms: int) -> dict | None:
        if not packets:
            return None
        return {
            "receivedAtMsStart": packets[0].received_ms,
            "receivedAtMsEnd": packets[-1].received_ms,
            "packetCount": len(packets),
            "windowStartMs": window_start_ms,
            "windowEndMs": window_end_ms,
        }

    # Compatibility helpers retained for old recordings and tests.  New live
    # capture never calls these combined-file methods.
    def _append_legacy(self, category: str, row: dict) -> None:
        if not self.recording_id:
            return
        with (self.root / category / f"{self.recording_id}.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps({"recordingId": self.recording_id, **row}, separators=(",", ":"), ensure_ascii=False) + "\n")

    def save_raw(self, *, timestamp_ms: int, valid: bool, invalid_reasons: list[str], payload: dict) -> None:
        self._append_legacy("raw", {"timestampMs": timestamp_ms, "valid": valid, "invalidReasons": invalid_reasons, "payload": payload})

    def save_algorithm(self, *, timestamp_ms: int, valid: bool, invalid_reasons: list[str], algorithm: dict) -> None:
        self._append_legacy("algorithm", {"timestampMs": timestamp_ms, "valid": valid, "invalidReasons": invalid_reasons, "algorithm": algorithm})

    def has_recording(self, recording_id: str | None) -> bool:
        if not recording_id:
            return False
        session = self._session_dir(recording_id)
        return session.is_dir() or (self.root / "raw" / f"{recording_id}.jsonl").exists() or (self.root / "algorithm" / f"{recording_id}.jsonl").exists()

    @staticmethod
    def _safe_recording_id(recording_id: str | None) -> bool:
        """Keep recording identifiers inside the persistent recording root."""
        return isinstance(recording_id, str) and re.fullmatch(r"rec-[0-9a-fA-F-]{1,64}", recording_id) is not None

    def completed_recordings(self) -> list[dict]:
        """Return completed session metadata suitable for the local download index."""
        recordings: list[dict] = []
        sessions = self.root / "sessions"
        for session in sessions.iterdir():
            if not session.is_dir() or not self._safe_recording_id(session.name) or session.name == self.recording_id:
                continue
            manifest = session / "manifest.json"
            started_at_ms = None
            if manifest.is_file():
                try:
                    started_at_ms = json.loads(manifest.read_text(encoding="utf-8")).get("startedAtMs")
                except (OSError, json.JSONDecodeError):
                    pass
            recordings.append({
                "recordingId": session.name,
                "startedAtMs": started_at_ms,
                "modifiedAtMs": int(session.stat().st_mtime * 1000),
            })
        return sorted(recordings, key=lambda item: item["modifiedAtMs"], reverse=True)

    def _new_events(self, recording_id: str) -> list[dict]:
        session = self._session_dir(recording_id)
        merged: dict[int, dict] = defaultdict(lambda: {"payload": {}, "valid": True, "invalidReasons": []})
        raw_windows: dict[int, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))

        for stream in {**RAW_STREAMS, **LEGACY_REPLAY_STREAMS}:
            path = session / "raw" / f"{stream}.jsonl"
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                timestamp = int(row["windowEndMs"])
                raw_windows[timestamp][stream].append(row)
                item = merged[timestamp]
                item["timestampMs"] = timestamp
                item["valid"] = item["valid"] and bool(row["valid"])
                item["invalidReasons"] = sorted(set(item["invalidReasons"] + row.get("invalidReasons", [])))

        for timestamp, streams in raw_windows.items():
            item = merged[timestamp]
            for stream, rows in streams.items():
                rows.sort(key=lambda row: int(row["sequence"]))
                raw = b"".join(base64.b64decode(row["bytesBase64"]) for row in rows)
                first = rows[0]
                # A prior, incorrect profile created this trace-only stream.
                # Preserve its files for historical recordings without exposing
                # them as the current FF51-based hr.raw stream.
                if stream == "hr_native":
                    continue
                item["payload"]["eegRaw" if stream == "eeg" else "hrRaw"] = {
                    "encoding": "base64",
                    "sampleFormat": "bytes",
                    "packetBytes": int(first["packetBytes"]),
                    "packetCount": len(rows),
                    "byteLength": len(raw),
                    "windowStartMs": int(first["windowStartMs"]),
                    "windowEndMs": timestamp,
                    "bytesBase64": base64.b64encode(raw).decode("ascii"),
                }

        for metric in ALGORITHM_FILES:
            path = session / "algorithm" / f"{metric}.jsonl"
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                timestamp = int(row["timestampMs"])
                item = merged[timestamp]
                item["timestampMs"] = timestamp
                item["valid"] = item["valid"] and bool(row["valid"])
                item["invalidReasons"] = sorted(set(item["invalidReasons"] + row.get("invalidReasons", [])))
                algorithm = item["payload"].setdefault("algorithm", {})
                _deep_merge(algorithm, row["algorithm"])
        return [merged[key] for key in sorted(merged)]

    def _legacy_events(self, recording_id: str) -> list[dict]:
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

    def events(self, recording_id: str) -> list[dict]:
        session = self._session_dir(recording_id)
        has_new_records = session.is_dir() and any(path.stat().st_size for path in session.rglob("*.jsonl"))
        return self._new_events(recording_id) if has_new_records else self._legacy_events(recording_id)

    def _write_manifest(self, recording_id: str, documentation_pdf: Path | None = None) -> dict:
        session = self._session_dir(recording_id)
        files = []
        for path in sorted(session.rglob("*.jsonl")):
            lines = path.read_text(encoding="utf-8").splitlines()
            timestamps = []
            for line in lines:
                row = json.loads(line)
                timestamp = row.get("timestampMs", row.get("receivedAtMs"))
                if isinstance(timestamp, int):
                    timestamps.append(timestamp)
            files.append({
                "path": str(path.relative_to(session)),
                "records": len(lines),
                "timestampStartMs": min(timestamps) if timestamps else None,
                "timestampEndMs": max(timestamps) if timestamps else None,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            })
        manifest = {
            "formatVersion": ARCHIVE_FORMAT_VERSION,
            "sessionId": recording_id,
            "startedAtMs": self._session_started_at_ms.get(recording_id),
            "files": files,
        }
        if documentation_pdf is not None:
            manifest["documentation"] = {
                "path": documentation_pdf.name,
                "title": "头环数据采集包格式说明",
                "version": CAPTURE_PACKAGE_DOCUMENT_VERSION,
                "sha256": sha256(documentation_pdf.read_bytes()).hexdigest(),
            }
        (session / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest

    def _export_documentation_pdf(self) -> Path:
        """Return the published capture-package PDF, rebuilding it when needed."""
        if self._capture_package_pdf is not None:
            pdf = self._capture_package_pdf.resolve()
            if not pdf.is_file():
                raise FileNotFoundError(f"Capture package documentation PDF is unavailable: {pdf}")
            return pdf

        if not CAPTURE_PACKAGE_MARKDOWN.is_file() or not CAPTURE_PACKAGE_RENDERER.is_file():
            raise FileNotFoundError("Published capture package documentation source is unavailable")
        output_dir = self.root / "exports" / ".documentation"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / CAPTURE_PACKAGE_DOCUMENT_FILENAME
        if not output.is_file() or output.stat().st_mtime < CAPTURE_PACKAGE_MARKDOWN.stat().st_mtime:
            try:
                subprocess.run(
                    [str(CAPTURE_PACKAGE_RENDERER), str(CAPTURE_PACKAGE_MARKDOWN), str(output)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except (OSError, subprocess.CalledProcessError) as error:
                detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
                raise RuntimeError(f"Cannot build capture package documentation PDF: {detail}") from error
        return output

    def export(self, recording_id: str) -> Path:
        if not self._safe_recording_id(recording_id) or not self._session_dir(recording_id).is_dir():
            raise FileNotFoundError(f"Recording {recording_id} is not available for export")
        session = self._session_dir(recording_id)
        documentation_pdf = self._export_documentation_pdf()
        self._write_manifest(recording_id, documentation_pdf)
        target = self.root / "exports" / f"neurobridge-{recording_id}.zip"
        temporary = target.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(session.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=f"{session.name}/{path.relative_to(session)}")
            archive.write(documentation_pdf, arcname=f"{session.name}/{documentation_pdf.name}")
        temporary.replace(target)
        return target
