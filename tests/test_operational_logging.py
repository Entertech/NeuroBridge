from __future__ import annotations

from hashlib import sha256
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from neurobridge.__main__ import _file_sha256
from neurobridge.logging_setup import utc_formatter
from neurobridge.adapters.observability import RuntimeProgress


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
