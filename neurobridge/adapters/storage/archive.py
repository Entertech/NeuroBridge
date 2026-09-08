"""Compatibility projections of segmented records, without duplicate live writes."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
import queue
from threading import Event, Thread
import uuid

from ...business.recording import RecordingStore
from ...domain.algorithm import AlgorithmResult
from ...domain.result import WindowResult
from ...domain.signal import ParsedSignal, ParsedSignalBatch
from ...ports.replay import ReplayEvent
from ..northbound.protocol import project_window


class SegmentedArchive(RecordingStore):
    """Own session identity; the repository alone owns live disk writes."""

    def start(self, started_at_ms=None):
        # Reuse identity generation, but suppress legacy file creation.
        import time
        import uuid
        self.recording_id = f"rec-{uuid.uuid4()}"
        self.last_recording_id = self.recording_id
        self._started_at_ms = started_at_ms if started_at_ms is not None else int(time.time() * 1000)
        self._session_started_at_ms[self.recording_id] = self._started_at_ms
        return self.recording_id

    def stop(self):
        self.last_recording_id = self.recording_id or self.last_recording_id
        self.recording_id = None
        self._started_at_ms = None
        self._session_started_at_ms.clear()

    @contextmanager
    def _algorithm_index(self, session):
        # Disk-backed correlation avoids retaining a 24-hour session in RAM.
        with tempfile.TemporaryDirectory(prefix="neurobridge-index-") as directory:
            connection = sqlite3.connect(str(Path(directory) / "index.db"), check_same_thread=False)
            try:
                connection.execute("PRAGMA cache_size=-2048")
                connection.execute("CREATE TABLE results (batch TEXT PRIMARY KEY, payload TEXT)")
                for path in sorted((session / "algorithm").glob("[0-9]*.jsonl")):
                    with path.open(encoding="utf-8") as source:
                        for line in source:
                            row = json.loads(line)
                            if row["recordType"] == "algorithm.result":
                                connection.execute("INSERT OR REPLACE INTO results VALUES (?, ?)",
                                                   (row["correlationId"], json.dumps(row["payload"])))
                yield connection
            finally:
                connection.close()

    def results(self, recording_id):
        if not self._safe_recording_id(recording_id):
            raise ValueError("Invalid recording session identifier")
        session = self._session_dir(recording_id)
        with self._algorithm_index(session) as algorithms:
            yield from self._results(recording_id, session, algorithms)

    def _results(self, recording_id, session, algorithms):
        for path in sorted((session / "parsed").glob("[0-9]*.jsonl")):
            with path.open(encoding="utf-8") as source:
                for line in source:
                    row = json.loads(line)
                    if row["recordType"] != "parsed.signal_batch":
                        continue
                    p = row["payload"]
                    signals = tuple(ParsedSignal(
                        s["signalType"], base64.b64decode(s["samples"]["bytesBase64"], validate=True),
                        s["sampleFormat"], s["unit"], s["windowHint"], tuple(s["frameRefs"]),
                        s["receivedAtMs"], s["valid"], tuple(s["invalidReasons"]),
                    ) for s in p["signals"])
                    batch = ParsedSignalBatch(p["batchId"], p["deviceProtocol"], p["connectionSessionId"],
                                              recording_id, p["windowStartMs"], p["windowEndMs"],
                                              signals, tuple(p["frameRefs"]), p["valid"], tuple(p["invalidReasons"]))
                    saved = algorithms.execute("SELECT payload FROM results WHERE batch=?", (batch.batch_id,)).fetchone()
                    a = json.loads(saved[0]) if saved else None
                    result = AlgorithmResult(batch.batch_id, a.get("algorithmVersion") if a else None,
                        a["startedAtMs"] if a else batch.window_end_ms,
                        a["completedAtMs"] if a else batch.window_end_ms,
                        a["metrics"] if a else {}, a["valid"] if a else False,
                        tuple(a["invalidReasons"]) if a else ("ALGORITHM_RESULT_MISSING",))
                    yield WindowResult(batch, result, "replay", result.completed_at_ms, True)

    def events(self, recording_id):
        return list(self.iter_events(recording_id))

    def iter_events(self, recording_id):
        if not self._safe_recording_id(recording_id):
            return
        with self._pin(recording_id):
            yield from self._iter_events(recording_id)

    @contextmanager
    def _pin(self, recording_id):
        session = self._session_dir(recording_id)
        if not session.is_dir():
            yield
            return
        marker = session / ".in-use"
        marker.mkdir(mode=0o700, exist_ok=True)
        token = marker / uuid.uuid4().hex
        token.touch(mode=0o600)
        try:
            yield
        finally:
            token.unlink(missing_ok=True)
            try:
                marker.rmdir()
            except OSError:
                pass  # Another reader still owns a pin.

    def _iter_events(self, recording_id):
        if not any((self._session_dir(recording_id) / "parsed").glob("[0-9]*.jsonl")):
            yield from super().events(recording_id)
            return
        for result in self.results(recording_id):
            payload, _, _ = project_window(result)
            payload["algorithm"] = dict(result.algorithm_result.metrics)
            yield {"timestampMs": result.batch.window_end_ms, "payload": payload,
                   "deviceProtocol": result.batch.device_protocol,
                   "valid": result.valid, "invalidReasons": list(dict.fromkeys(result.batch.invalid_reasons + result.algorithm_result.invalid_reasons)),
                   "rawValid": result.batch.valid, "rawInvalidReasons": result.batch.invalid_reasons}

    def export(self, recording_id):
        if not self._safe_recording_id(recording_id) or recording_id == self.recording_id:
            raise FileNotFoundError("Only completed recordings may be exported")
        session = self._session_dir(recording_id)
        if not any((session / "parsed").glob("[0-9]*.jsonl")):
            return super().export(recording_id)
        # Export the released per-packet/per-metric format from a separate
        # staging directory. Never zip internal raw/parsed/partial segments.
        marker = session / ".exporting"
        with marker.open("x"):
            pass
        try:
            with tempfile.TemporaryDirectory(prefix="neurobridge-export-") as directory:
                projected = RecordingStore(Path(directory), self._export_documentation_pdf())
                generated_id = projected.start()
                projected._session_dir(generated_id).rename(projected._session_dir(recording_id))
                projected.recording_id = recording_id
                manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
                projected._session_started_at_ms[recording_id] = manifest.get("startedAtMs")
                for result in self.results(recording_id):
                    _, refs, grouped = project_window(result)
                    for stream, signals in grouped.items():
                        for signal in signals:
                            projected.save_raw_packet(stream=stream, received_at_ms=signal.received_at_ms,
                                window_start_ms=result.batch.window_start_ms, window_end_ms=result.batch.window_end_ms,
                                value=signal.samples)
                    projected.save_algorithm_events(algorithm=dict(result.algorithm_result.metrics),
                        computed_at_ms=result.completed_at_ms, eeg_source=refs["eeg"], hr_source=refs["hr"],
                        valid=result.valid, invalid_reasons=list(dict.fromkeys(result.batch.invalid_reasons + result.algorithm_result.invalid_reasons)))
                output = projected.export(recording_id)
                target = self.root / "exports" / output.name
                temporary = target.with_suffix(".zip.tmp")
                shutil.copyfile(output, temporary)
                temporary.replace(target)
                return target
        finally:
            marker.unlink(missing_ok=True)


class ArchiveReplayReader:
    def __init__(self, archive):
        self.archive = archive
        self._inspection = None

    async def inspect(self, preferred_recording_id):
        if self._inspection is None or self._inspection.done():
            self._inspection = asyncio.create_task(self._inspect_async(preferred_recording_id))
        return await asyncio.shield(self._inspection)

    async def _inspect_async(self, preferred_recording_id):
        async for value in self._read(lambda: iter((self._inspect(preferred_recording_id),))):
            return value

    @staticmethod
    async def _read(factory):
        """One bounded daemon reader; stalled files never pin asyncio's executor."""
        outbox = queue.Queue(maxsize=1)
        stopped = Event()
        end = object()
        def offer(value):
            while not stopped.is_set():
                try:
                    outbox.put(value, timeout=0.05)
                    return
                except queue.Full:
                    pass
        def run():
            rows = None
            try:
                rows = factory()
                for row in rows:
                    if stopped.is_set():
                        break
                    offer((row, None))
            except Exception as error:
                offer((None, error))
            finally:
                if rows is not None and hasattr(rows, "close"):
                    rows.close()
                offer((end, None))
        Thread(target=run, name="neurobridge-replay-reader", daemon=True).start()
        try:
            while True:
                try:
                    value, error = outbox.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.005)
                    continue
                if error is not None:
                    raise error
                if value is end:
                    return
                yield value
        finally:
            stopped.set()

    def _inspect(self, preferred_recording_id):
        candidates = [preferred_recording_id] if preferred_recording_id else []
        candidates += [item["recordingId"] for item in self.archive.completed_recordings()]
        for category in ("raw", "algorithm"):
            candidates += [p.stem for p in sorted((self.archive.root / category).glob("rec-*.jsonl"),
                                                 key=lambda p: p.stat().st_mtime, reverse=True)]
        for recording_id in dict.fromkeys(candidates):
            if recording_id == self.archive.recording_id:
                continue
            found, latest, timestamp, streams = False, None, None, set()
            for row in self.archive.iter_events(recording_id):
                if row.get("deviceProtocol") not in {None, "headband_ble"}:
                    continue
                found = True
                algorithm = row["payload"].get("algorithm", {})
                if any(k not in {"hr", "pressure", "coherence", "arousal"} for k in algorithm):
                    streams.add("eeg")
                if any(k in algorithm for k in {"hr", "pressure", "coherence", "arousal"}):
                    streams.add("hr")
                if row["valid"] and algorithm:
                    latest, timestamp = algorithm, row["timestampMs"]
            if found:
                return recording_id, frozenset(streams), latest, timestamp
        return None, frozenset(), None, None

    async def events(self, recording_session_id):
        async for row in self._read(lambda: self.archive.iter_events(recording_session_id)):
            if row.get("deviceProtocol") not in {None, "headband_ble"}:
                continue
            yield ReplayEvent(recording_session_id, row["timestampMs"], row["payload"],
                              row["valid"], tuple(row.get("invalidReasons", ())),
                              row.get("rawValid"), tuple(row.get("rawInvalidReasons", ())))
