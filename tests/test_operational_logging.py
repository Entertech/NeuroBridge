from __future__ import annotations

from hashlib import sha256
from datetime import datetime, timezone
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from neurobridge.__main__ import _file_sha256
from neurobridge.logging_setup import TimestampedRotatingFileHandler, utc_formatter
from neurobridge.download import _log_archive
from neurobridge.adapters.observability import RuntimeProgress


class TimestampedLogRotationTests(unittest.TestCase):
    def test_timestamp_rotation_is_unique_across_restart_with_frozen_clock(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("neurobridge.logging_setup.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 9, 12, 34, 56, 123456, tzinfo=timezone.utc)
            logfile = Path(directory) / "neurobridge.log"
            expected = [f"第{i:02d}条完整日志" for i in range(12)]
            archived_before_restart = {}
            for start in (0, 6):
                handler = TimestampedRotatingFileHandler(logfile, maxBytes=50, backupCount=20, encoding="utf-8")
                try:
                    for message in expected[start:start + 6]:
                        handler.emit(logging.makeLogRecord({"msg": message}))
                finally:
                    handler.close()
                if start == 0:
                    archived_before_restart = {p: p.read_bytes() for p in Path(directory).glob("neurobridge.log.*")}
            for path, contents in archived_before_restart.items():
                self.assertEqual(path.read_bytes(), contents)
            files = list(Path(directory).glob("neurobridge.log*"))
            self.assertTrue(all(p.stat().st_size <= 50 for p in files))
            lines = [line for p in files for line in p.read_text(encoding="utf-8").splitlines()]
            self.assertCountEqual(lines, expected)
            for path in files:
                if path != logfile:
                    self.assertRegex(path.name, r"^neurobridge\.log\.20260909T123456\.123456Z\.[a-z0-9_]{8}$")
            archive = _log_archive(Path(directory), logfile.name)
            with zipfile.ZipFile(archive) as bundle:
                for path in files:
                    self.assertEqual(bundle.read(path.name), path.read_bytes())

    def test_retention_includes_old_numbered_backups_but_preserves_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("neurobridge.log.1", "neurobridge.log.2", "neurobridge.log.3"):
                (root / name).write_text("legacy\n", encoding="utf-8")
                os.utime(root / name, (1, 1))
            unrelated = root / "neurobridge.log.manual-copy"
            unrelated.write_text("keep\n", encoding="utf-8")
            handler = TimestampedRotatingFileHandler(root / "neurobridge.log", maxBytes=20, backupCount=2, encoding="utf-8")
            try:
                for i in range(8):
                    handler.emit(logging.makeLogRecord({"msg": f"record {i:02d} payload"}))
            finally:
                handler.close()
            self.assertFalse(list(root.glob("neurobridge.log.[123]")))
            self.assertEqual(len(list(root.glob("neurobridge.log*"))), 4)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep\n")
            self.assertIn("record 07", (root / "neurobridge.log").read_text(encoding="utf-8"))

    def test_oversized_record_is_preserved_and_next_record_rotates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "neurobridge.log"
            handler = TimestampedRotatingFileHandler(path, maxBytes=20, backupCount=2, encoding="utf-8")
            try:
                handler.emit(logging.makeLogRecord({"msg": "x" * 100}))
                self.assertFalse(list(path.parent.glob("neurobridge.log.*")))
                handler.emit(logging.makeLogRecord({"msg": "next"}))
            finally:
                handler.close()
            self.assertEqual(path.read_text(encoding="utf-8"), "next\n")
            self.assertEqual(next(path.parent.glob("neurobridge.log.*")).read_text(encoding="utf-8"), "x" * 100 + "\n")

    def test_locked_archive_does_not_drop_new_logs_and_cleanup_retries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "neurobridge.log"
            legacy = path.with_name("neurobridge.log.1")
            legacy.write_text("legacy\n", encoding="utf-8")
            handler = TimestampedRotatingFileHandler(path, maxBytes=10, backupCount=1, encoding="utf-8")
            try:
                handler.emit(logging.makeLogRecord({"msg": "first"}))
                with patch.object(Path, "unlink", side_effect=PermissionError("locked")), patch("neurobridge.logging_setup.sys.stderr") as stderr:
                    handler.emit(logging.makeLogRecord({"msg": "second"}))
                    self.assertTrue(stderr.write.called)
                self.assertEqual(path.read_text(encoding="utf-8"), "second\n")
                self.assertTrue(legacy.exists())
                handler.emit(logging.makeLogRecord({"msg": "third"}))
                self.assertEqual(path.read_text(encoding="utf-8"), "third\n")
                self.assertEqual(len(list(path.parent.glob("neurobridge.log.*"))), 1)
            finally:
                handler.close()


class OperationalLoggingTests(unittest.TestCase):
    def test_progress_uses_elapsed_monotonic_time_and_keeps_only_last_counters(self) -> None:
        with patch("neurobridge.adapters.observability.time.monotonic", side_effect=[100, 110, 130, 140]):
            progress = RuntimeProgress()
            first = progress.sample({"frames": 50, "algorithm_invalid_results": 1})
            second = progress.sample({"frames": 70, "algorithm_invalid_results": 3})
            idle = progress.sample({"frames": 70, "algorithm_invalid_results": 3})
        self.assertEqual(first["framesPerSecond"], 5)
        self.assertEqual(second["uptimeSeconds"], 30)
        self.assertEqual(second["intervalSeconds"], 20)
        self.assertEqual(second["delta"], {"frames": 20, "algorithm_invalid_results": 2})
        self.assertEqual(second["framesPerSecond"], 1)
        self.assertEqual(idle["delta"]["frames"], 0)
        self.assertEqual(idle["sampleSequence"], 3)

    def test_bootstrap_does_not_capture_foreground_runtime_output(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "linux/neurobridge-kylin-bootstrap.sh").read_text()
        launch = source[source.index('bootstrap_log="$project_dir/.runtime/logs/kylin-bootstrap-'):]
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".runtime/logs").mkdir(parents=True)
            assistant = project / "assistant.sh"
            assistant.write_text('printf "runtime-stdout\\n"; printf "runtime-stderr\\n" >&2\n')
            result = subprocess.run(["bash", "-c", launch], capture_output=True, text=True,
                env={**os.environ, "project_dir": directory, "assistant": str(assistant)})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("runtime-stdout", result.stdout)
            self.assertIn("runtime-stderr", result.stderr)
            logged = next((project / ".runtime/logs").glob("kylin-bootstrap-*.log")).read_text()
            self.assertIn("sourceUpdateAttempted=false", logged)
            self.assertNotIn("runtime-stdout", logged)
            self.assertNotIn("runtime-stderr", logged)

    def test_menu_foreground_start_does_not_tee_into_setup_log(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "linux/setup-kylin-gateway.sh").read_text()
        function = source.split('start_gateway() {', 1)[1].split('\n}\n', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "linux").mkdir()
            start = project / "linux/start-kylin-gateway.sh"
            start.write_text('#!/bin/sh\nprintf "runtime-output\\n"; exit 7\n')
            start.chmod(0o755)
            preference = project / "preference"
            preference.write_text("enabled=false\n")
            # run_step must not receive the long-running foreground process.
            harness = 'systemctl() { return 1; }; log_message() { :; }; run_step() { echo unexpected-tee; return 99; };\n'
            harness += 'start_gateway() {' + function + '\n}; start_gateway'
            result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                env={**os.environ, "root_dir": directory, "autostart_preference_path": str(preference)})
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(result.stdout, "runtime-output\n")

    def test_kylin_runtime_output_has_only_one_bounded_persistent_copy(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "linux/start-kylin-gateway.sh").read_text()
        # Run the real post-validation launch path with a synthetic Python
        # executable, skipping host/TTY checks on the development machine.
        launch = source[source.index('install -d -m 0750 \\\n'):]
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            producer = temporary / "producer.py"
            producer.write_text('''import logging, os
from pathlib import Path
from neurobridge.config import LoggingConfig
from neurobridge.logging_setup import configure_logging
configure_logging(LoggingConfig(directory=Path(os.environ["runtime_dir"]) / "logs", max_bytes=1024, backup_count=2))
for i in range(1000): logging.info("synthetic operational message %s", i)
''')
            wrapper = temporary / "python-fixture"
            wrapper.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(producer))}\n")
            wrapper.chmod(0o755)
            runtime = temporary / "runtime"
            (runtime / "logs").mkdir(parents=True)
            old_console = runtime / "logs/neurobridge-console.log"
            old_console.write_text("previous run\n")
            result = subprocess.run(["bash", "-c", launch], cwd=root, capture_output=True, text=True,
                env={**os.environ, "root_dir": str(root), "runtime_dir": str(runtime),
                     "python_path": str(wrapper), "config_path": "fixture", "transport": "bluetooth"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("synthetic operational message 999", result.stderr)
            self.assertEqual(old_console.read_text(), "previous run\n")
            logs = list((runtime / "logs").glob("neurobridge.log*"))
            self.assertEqual(len(logs), 3)
            self.assertTrue(all(path.stat().st_size <= 1024 for path in logs))

    def test_configuration_identity_uses_hash_without_exposing_contents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "gateway.toml"
            contents = b'[server]\nhost = "192.168.88.10"\n'
            config.write_bytes(contents)
            self.assertEqual(_file_sha256(str(config)), sha256(contents).hexdigest())
            self.assertEqual(_file_sha256(str(config.with_name("missing.toml"))), "unavailable:FileNotFoundError")

    def test_operational_log_timestamp_is_utc_with_milliseconds(self) -> None:
        record = logging.LogRecord(
            name="neurobridge.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="diagnostic event",
            args=(),
            exc_info=None,
        )
        record.created = 0.123
        record.msecs = 123.0
        formatted = utc_formatter().format(record)
        self.assertRegex(
            formatted,
            r"^1970-01-01T00:00:00\.123Z pid=\d+ INFO neurobridge\.test: diagnostic event$",
        )
