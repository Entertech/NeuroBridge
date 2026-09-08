"""Bounded, crash-recoverable JSONL recording repository."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import queue
import re
import shutil
from threading import Event, Lock, Thread
import time
from typing import Any
import uuid

from ...domain.status import StorageState
from ...application.status import StorageHealthTracker
from ...ports.recording import PersistenceReceipt, PersistenceRecord, StorageStatus


LOG = logging.getLogger(__name__)


@dataclass
class _Segment:
    session_id: str
    category: str
    sequence: int
    path: Path
    file: Any
    opened_at_ms: int
    first_at_ms: int | None = None
    last_at_ms: int | None = None
    count: int = 0
    bytes_written: int = 0


@dataclass
class _PendingWrite:
    record: PersistenceRecord
    event: Event
    succeeded: bool | None = None
    reason: str | None = None


@dataclass
class _CloseSession:
    session_id: str
    event: Event


class SegmentedRecordingRepository:
    """Keep disk latency outside acquisition and never use an unbounded queue."""

    def __init__(
        self,
        root: str | Path,
        *,
        queue_size: int = 1024,
        segment_duration_seconds: int = 600,
        segment_max_bytes: int = 256 * 1024**2,
        warning_threshold_bytes: int = 5 * 1024**3,
        critical_threshold_bytes: int = 1 * 1024**3,
        recovery_margin_bytes: int = 1 * 1024**3,
        recovery_checks: int = 3,
        fsync_interval_records: int = 64,
        session_retention_days: int = 30,
        auto_cleanup_enabled: bool = False,
        shutdown_timeout_ms: int = 5000,
    ) -> None:
        if queue_size <= 0 or segment_duration_seconds <= 0 or segment_max_bytes <= 0:
            raise ValueError("Recording limits must be positive")
        if shutdown_timeout_ms <= 0:
            raise ValueError("Recording shutdown timeout must be positive")
        if recovery_margin_bytes <= 0 or recovery_checks <= 0 or fsync_interval_records <= 0 or session_retention_days <= 0:
            raise ValueError("Storage recovery, fsync, and retention settings must be positive")
        if critical_threshold_bytes >= warning_threshold_bytes:
            raise ValueError("critical threshold must be below warning threshold")
        self.root = Path(root)
        self.sessions_root = self.root / "sessions"
        self.quarantine = self.root / "quarantine"
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self.quarantine.mkdir(parents=True, exist_ok=True)
        self.segment_duration_ms = segment_duration_seconds * 1000
        self.segment_max_bytes = segment_max_bytes
        self.warning_threshold_bytes = warning_threshold_bytes
        self.critical_threshold_bytes = critical_threshold_bytes
        self.recovery_margin_bytes = recovery_margin_bytes
        self.recovery_checks = recovery_checks
        self.fsync_interval_records = fsync_interval_records
        self.session_retention_days = session_retention_days
        self.auto_cleanup_enabled = auto_cleanup_enabled
        self.shutdown_timeout_ms = shutdown_timeout_ms
        self._queue: queue.Queue[_PendingWrite | _CloseSession] = queue.Queue(maxsize=queue_size)
        self._segments: dict[tuple[str, str], _Segment] = {}
        self._lock = Lock()
        self._state = StorageState.OK
        self._available_bytes: int | None = None
        self._affected_from_ms: int | None = None
        self._last_success_at_ms: int | None = None
        self._gap_count = 0
        self._last_failure_reason: str | None = None
        self._stopped = False
        self._health = StorageHealthTracker(
            warning_threshold_bytes,
            critical_threshold_bytes,
            recovery_margin_bytes,
            recovery_checks,
        )
        self.recover_partials()
        self._refresh_capacity()
        self._worker = Thread(target=self._writer, name="neurobridge-recording", daemon=True)
        self._worker.start()

    def try_append(self, record: PersistenceRecord) -> PersistenceReceipt:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", record.recording_session_id) is None:
            return self._record_gap(record.captured_at_ms, "invalid_session_id")
        if self._stopped:
            return self._record_gap(record.captured_at_ms, "repository_stopped")
        pending = _PendingWrite(record, Event())
        try:
            self._queue.put_nowait(pending)
        except queue.Full:
            return self._record_gap(record.captured_at_ms, "writer_queue_full", StorageState.ERROR)
        return PersistenceReceipt(True, False, "write_pending", pending)

    async def confirm(self, receipt: PersistenceReceipt) -> PersistenceReceipt:
        if not receipt.accepted or receipt.confirmation is None:
            return receipt
        if not isinstance(receipt.confirmation, _PendingWrite):
            return PersistenceReceipt(False, False, "confirmation_not_found")
        pending = receipt.confirmation
        # Polling is cancellation-safe; never strand an executor thread in
        # Event.wait() when a disk write stalls indefinitely.
        try:
            async with asyncio.timeout(self.shutdown_timeout_ms / 1000):
                while not pending.event.is_set():
                    await asyncio.sleep(0.005)
        except TimeoutError:
            return PersistenceReceipt(True, False, "confirmation_timeout")
        if pending.succeeded:
            return PersistenceReceipt(True, True)
        return PersistenceReceipt(False, False, pending.reason or "write_error")

    async def close_session(self, recording_session_id: str) -> None:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", recording_session_id) is None:
            raise ValueError("Invalid recording session identifier")
        command = _CloseSession(recording_session_id, Event())
        try:
            async with asyncio.timeout(self.shutdown_timeout_ms / 1000):
                while True:
                    try:
                        self._queue.put_nowait(command)
                        break
                    except queue.Full:
                        await asyncio.sleep(0.005)
                while not command.event.is_set():
                    await asyncio.sleep(0.005)
        except TimeoutError:
            self._record_gap(int(time.time() * 1000), "session_close_timeout", StorageState.ERROR)
            LOG.error("Recording session close timed out; partial files retained: recordingSessionId=%s", recording_session_id)

    def storage_status(self) -> StorageStatus:
        with self._lock:
            return StorageStatus(
                self._state,
                self._available_bytes,
                self._state == StorageState.OK,
                self._affected_from_ms,
                self._last_success_at_ms,
                self._gap_count,
                {
                    "queueDepth": self._queue.qsize(),
                    "queueCapacity": self._queue.maxsize,
                    "lastFailureReason": self._last_failure_reason,
                    **self._health.status(self._available_bytes).details,
                },
            )

    async def close(self) -> None:
        self._stopped = True
        try:
            async with asyncio.timeout(self.shutdown_timeout_ms / 1000):
                while self._worker.is_alive():
                    await asyncio.sleep(0.005)
        except TimeoutError:
            self._record_gap(int(time.time() * 1000), "writer_shutdown_timeout", StorageState.ERROR)
            LOG.error("Recording writer shutdown timed out; unfinished segments remain recoverable as partial files")

    def _writer(self) -> None:
        while True:
            try:
                first = self._queue.get(timeout=0.05)
            except queue.Empty:
                if not self._stopped:
                    continue
                try:
                    for segment in tuple(self._segments.values()):
                        self._finalize_segment(segment)
                    self._segments.clear()
                except Exception:
                    LOG.exception("Recording finalization failed; partial files retained")
                return
            batch = [first]
            while len(batch) < self.fsync_interval_records and not isinstance(batch[-1], _CloseSession):
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            for pending in batch:
                try:
                    if isinstance(pending, _CloseSession):
                        self._close_session_sync(pending.session_id)
                    else:
                        self._write(pending.record)
                        pending.succeeded = True
                except Exception as error:
                    if isinstance(pending, _PendingWrite):
                        pending.succeeded = False
                        pending.reason = "write_error"
                        self._record_gap(pending.record.captured_at_ms, "write_error", StorageState.ERROR)
                        LOG.exception("Recording write failed: recordingSessionId=%s recordType=%s capturedAtMs=%s errorType=%s",
                                      pending.record.recording_session_id, pending.record.record_type,
                                      pending.record.captured_at_ms, type(error).__name__)
                    else:
                        LOG.exception("Recording session finalization failed: recordingSessionId=%s", pending.session_id)
            try:
                # Confirm the whole bounded batch only after fsync, amortizing
                # disk latency without mistaking buffered writes for durability.
                for segment in tuple(self._segments.values()):
                    segment.file.flush()
                    os.fsync(segment.file.fileno())
                self._last_success_at_ms = int(time.time() * 1000)
                self._refresh_capacity_locked(write_succeeded=all(
                    p.succeeded for p in batch if isinstance(p, _PendingWrite)))
            except Exception:
                for pending in batch:
                    if isinstance(pending, _PendingWrite) and pending.succeeded:
                        pending.succeeded = False
                        pending.reason = "fsync_error"
                        self._record_gap(pending.record.captured_at_ms, "fsync_error", StorageState.ERROR)
                LOG.exception("Recording batch sync failed; persistence is not guaranteed")
            finally:
                for pending in batch:
                    pending.event.set()
                    self._queue.task_done()

    def _write(self, record: PersistenceRecord) -> None:
        category = self._category(record.record_type)
        encoded = (json.dumps(self._json_value(record), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        segment = self._segments.get((record.recording_session_id, category))
        if segment is not None and segment.file.closed:
            # A rename/manifest failure may have closed the previous file.
            # Open a new sequence on the next write so recovery is possible.
            self._segments.pop((record.recording_session_id, category), None)
            segment = None
        if segment is None:
            segment = self._open_segment(record.recording_session_id, category)
            self._segments[(record.recording_session_id, category)] = segment
        elapsed = record.captured_at_ms - segment.opened_at_ms
        if segment.count and (elapsed >= self.segment_duration_ms or segment.bytes_written + len(encoded) > self.segment_max_bytes):
            self._finalize_segment(segment)
            segment = self._open_segment(record.recording_session_id, category, segment.sequence + 1)
            self._segments[(record.recording_session_id, category)] = segment
        segment.file.write(encoded)
        segment.count += 1
        segment.bytes_written += len(encoded)
        segment.first_at_ms = segment.first_at_ms if segment.first_at_ms is not None else record.captured_at_ms
        segment.last_at_ms = record.captured_at_ms
        should_cleanup = self.auto_cleanup_enabled and self._state in {StorageState.FULL, StorageState.WARNING}
        if should_cleanup:
            self.cleanup_completed_sessions()

    def _open_segment(self, session_id: str, category: str, sequence: int | None = None) -> _Segment:
        directory = self.sessions_root / session_id / category
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if sequence is None:
            existing = [int(path.name.split(".", 1)[0]) for path in directory.glob("[0-9][0-9][0-9][0-9][0-9][0-9].jsonl*")]
            sequence = max(existing, default=0) + 1
        path = directory / f"{sequence:06d}.jsonl.partial"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        return _Segment(session_id, category, sequence, path, os.fdopen(descriptor, "ab"), int(time.time() * 1000))

    def _finalize_segment(self, segment: _Segment, *, recovered: bool = False) -> None:
        segment.file.flush()
        os.fsync(segment.file.fileno())
        segment.file.close()
        final = segment.path.with_suffix("")
        os.replace(segment.path, final)
        digest = sha256()
        with final.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        entry = {
            "path": str(final.relative_to(self.sessions_root / segment.session_id)),
            "category": segment.category,
            "sequence": segment.sequence,
            "recordCount": segment.count,
            "firstCapturedAtMs": segment.first_at_ms,
            "lastCapturedAtMs": segment.last_at_ms,
            "byteLength": final.stat().st_size,
            "sha256": digest.hexdigest(),
            "status": "recovered" if recovered else "closed",
        }
        self._update_manifest(segment.session_id, entry)

    def _update_manifest(self, session_id: str, entry: dict[str, object] | None, *, ended: bool = False) -> None:
        directory = self.sessions_root / session_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {"schemaVersion": 1, "recordingSessionId": session_id, "segments": [], "startedAtMs": int(time.time() * 1000)}
        if entry is not None:
            manifest["segments"] = [item for item in manifest.get("segments", []) if item.get("path") != entry.get("path")]
            manifest["segments"].append(entry)
        if ended:
            manifest["endedAtMs"] = int(time.time() * 1000)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with temporary.open("rb") as source:
            os.fsync(source.fileno())
        os.replace(temporary, path)

    def _close_session_sync(self, session_id: str) -> None:
        keys = [key for key in self._segments if key[0] == session_id]
        for key in keys:
            segment = self._segments.pop(key)
            self._finalize_segment(segment)
        self._update_manifest(session_id, None, ended=True)

    def recover_partials(self) -> None:
        for path in self.sessions_root.glob("*/**/*.jsonl.partial"):
            try:
                data = path.read_bytes()
                complete = data[: data.rfind(b"\n") + 1] if b"\n" in data else b""
                rows = [json.loads(line) for line in complete.splitlines() if line]
                if not rows:
                    raise ValueError("partial segment has no complete record")
                path.write_bytes(complete)
                session_id = path.parents[1].name
                category = path.parent.name
                sequence = int(path.name.split(".", 1)[0])
                segment = _Segment(session_id, category, sequence, path, path.open("ab"), rows[0]["capturedAtMs"], rows[0]["capturedAtMs"], rows[-1]["capturedAtMs"], len(rows), len(complete))
                self._finalize_segment(segment, recovered=True)
            except Exception:
                destination = self.quarantine / f"{path.parents[1].name}-{path.parent.name}-{path.name}"
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(path, destination)
                self._last_failure_reason = "partial_recovery_failed"
                self._state = self._health.observe(self._available_bytes or 0, write_succeeded=False)

    def cleanup_completed_sessions(self) -> tuple[str, ...]:
        """Delete eligible completed sessions oldest-first when explicitly enabled."""

        if not self.auto_cleanup_enabled:
            return ()
        with self._lock:
            active_sessions = {session_id for session_id, _category in self._segments}
        candidates: list[tuple[int, Path]] = []
        for session in self.sessions_root.iterdir():
            if not session.is_dir() or session.name in active_sessions:
                continue
            manifest_path = session / "manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(manifest.get("endedAtMs"), int) or manifest.get("retain") is True:
                continue
            if (session / ".in-use").exists() or (session / ".exporting").exists():
                continue
            candidates.append((manifest["endedAtMs"], session))
        removed: list[str] = []
        recovery_target = self.warning_threshold_bytes + self.recovery_margin_bytes
        retention_cutoff_ms = int(time.time() * 1000) - self.session_retention_days * 24 * 60 * 60 * 1000
        for _ended_at, session in sorted(candidates):
            if shutil.disk_usage(self.root).free >= recovery_target:
                break
            if _ended_at > retention_cutoff_ms:
                continue
            tombstone = self.root / f".cleanup-{session.name}-{uuid.uuid4().hex}"
            os.replace(session, tombstone)
            shutil.rmtree(tombstone)
            removed.append(session.name)
            self._append_cleanup_audit(session.name, "deleted")
        self._refresh_capacity()
        return tuple(removed)

    def _append_cleanup_audit(self, session_id: str, outcome: str) -> None:
        path = self.root / "cleanup-audit.jsonl"
        row = {"timestampMs": int(time.time() * 1000), "recordingSessionId": session_id, "outcome": outcome}
        with path.open("a", encoding="utf-8") as audit:
            audit.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _record_gap(self, timestamp_ms: int, reason: str, state: StorageState | None = None) -> PersistenceReceipt:
        with self._lock:
            self._gap_count += 1
            if self._affected_from_ms is None:
                self._affected_from_ms = timestamp_ms
            self._last_failure_reason = reason
            if state is not None:
                available = self._available_bytes if self._available_bytes is not None else 0
                self._state = self._health.observe(available, write_succeeded=False)
            if self._gap_count & (self._gap_count - 1) == 0:
                LOG.error("Persistence gap: reason=%s count=%s affectedFromMs=%s capturedAtMs=%s",
                          reason, self._gap_count, self._affected_from_ms, timestamp_ms)
        return PersistenceReceipt(False, False, reason)

    def _refresh_capacity(self) -> None:
        self._refresh_capacity_locked()

    def _refresh_capacity_locked(self, *, write_succeeded: bool | None = None) -> None:
        available = shutil.disk_usage(self.root).free
        with self._lock:
            self._available_bytes = available
            previous = self._state
            self._state = self._health.observe(available, write_succeeded=write_succeeded)
            if previous != StorageState.OK and self._state == StorageState.OK:
                LOG.info("Storage recovered: availableBytes=%s affectedFromMs=%s gapCount=%s", available, self._affected_from_ms, self._gap_count)
                self._affected_from_ms = None
                self._last_failure_reason = None

    @staticmethod
    def _category(record_type: str) -> str:
        prefix = record_type.split(".", 1)[0]
        return prefix if prefix in {"raw", "parsed", "algorithm"} else "parsed"

    @classmethod
    def _json_value(cls, record: PersistenceRecord) -> dict[str, object]:
        return {
            "schemaVersion": record.schema_version,
            "recordingSessionId": record.recording_session_id,
            "recordType": record.record_type,
            "capturedAtMs": record.captured_at_ms,
            "correlationId": record.correlation_id,
            "payload": cls._encode(record.payload),
        }

    @classmethod
    def _encode(cls, value: object) -> object:
        if isinstance(value, bytes):
            return {"encoding": "base64", "bytesBase64": base64.b64encode(value).decode("ascii")}
        if isinstance(value, dict):
            return {str(key): cls._encode(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._encode(item) for item in value]
        return value
