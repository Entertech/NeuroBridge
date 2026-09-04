from __future__ import annotations

from hashlib import sha256
import logging
from pathlib import Path
import tempfile
import unittest

from neurobridge.__main__ import _file_sha256
from neurobridge.logging_setup import utc_formatter


class OperationalLoggingTests(unittest.TestCase):
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
