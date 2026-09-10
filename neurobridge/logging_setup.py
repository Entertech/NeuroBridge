from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler, WatchedFileHandler
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sys
import tempfile
import time

from .config import LoggingConfig


class TimestampedRotatingFileHandler(RotatingFileHandler):
    """Single-writer size rotation with immutable, uniquely reserved archive names."""

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self.stream is None:
            self.stream = self._open()
        self.stream.seek(0, os.SEEK_END)
        size = self.stream.tell()
        # Count encoded bytes, including Windows newline translation. Preserve
        # an oversized individual record intact rather than splitting a traceback.
        message = (self.format(record) + self.terminator).replace("\n", os.linesep)
        return self.maxBytes > 0 and size > 0 and size + len(message.encode(self.encoding, self.errors or "strict")) > self.maxBytes

    def doRollover(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        base = Path(self.baseFilename)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        # O_EXCL reservation prevents overwriting archives even if the clock
        # repeats, moves backwards, or the process restarts in the same instant.
        descriptor, filename = tempfile.mkstemp(prefix=f"{base.name}.{stamp}.", dir=base.parent)
        os.close(descriptor)  # Windows requires closed handles before replacement.
        archive = Path(filename)
        try:
            os.replace(base, archive)
        except OSError:
            archive.unlink(missing_ok=True)
            raise
        finally:
            if not self.delay:
                self.stream = self._open()
        try:
            self._prune_archives(base, archive)
        except OSError as error:
            self._report_retention_failure(base.name, error)

    @staticmethod
    def _report_retention_failure(filename: str, error: OSError) -> None:
        # Never log recursively through the handler while rolling it over.
        try:
            if sys.stderr is not None:
                sys.stderr.write(f"Log archive cleanup failed: file={filename} errorType={type(error).__name__}; retry at next rotation\n")
        except OSError:
            pass

    def _prune_archives(self, base: Path, newest: Path) -> None:
        pattern = re.compile(re.escape(base.name) + r"\.(?:[0-9]+|[0-9]{8}T[0-9]{6}\.[0-9]{6}Z\.[a-z0-9_]{8})\Z")
        candidates = []
        for path in base.parent.iterdir():
            if path != newest and pattern.fullmatch(path.name) and not path.is_symlink():
                try:
                    if path.is_file():
                        candidates.append((path.stat().st_mtime_ns, path.name, path))
                except FileNotFoundError:
                    continue
        # Always retain the archive just created, including after clock rollback.
        for _, _, path in sorted(candidates, reverse=True)[max(0, self.backupCount - 1):]:
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                # A reader/antivirus can temporarily lock a file on Windows.
                # Retry retention at the next rotation without dropping this record.
                self._report_retention_failure(path.name, error)


def utc_formatter() -> logging.Formatter:
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03dZ pid=%(process)d %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    # UTC with millisecond precision makes journal, device timestamps, and
    # logs exported from machines in different time zones directly comparable.
    formatter.converter = time.gmtime
    return formatter


def configure_logging(config: LoggingConfig) -> None:
    """Write durable operational logs while retaining systemd journal output.

    Size-based rotation bounds files on every platform, including source-tree
    Kylin runs. External mode is reserved for deployments managed by logrotate.
    """

    config.directory.mkdir(parents=True, exist_ok=True)
    logfile = config.directory / config.filename
    formatter = utc_formatter()
    file_handler = (WatchedFileHandler(logfile, encoding="utf-8")
                    if config.rotation_mode == "external" else
                    TimestampedRotatingFileHandler(logfile, maxBytes=config.max_bytes,
                                        backupCount=config.backup_count, encoding="utf-8"))
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logging.basicConfig(
        level=getattr(logging, config.level),
        handlers=[file_handler, stream_handler],
        force=True,
    )
