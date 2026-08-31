from __future__ import annotations

import logging
from logging.handlers import WatchedFileHandler

from .config import LoggingConfig


def configure_logging(config: LoggingConfig) -> None:
    """Write durable operational logs while retaining systemd journal output.

    WatchedFileHandler cooperates with Ubuntu's logrotate: the service keeps
    running and reopens the new log file on its next emitted record.
    """

    config.directory.mkdir(parents=True, exist_ok=True)
    logfile = config.directory / config.filename
    formatter = logging.Formatter("%(asctime)s pid=%(process)d %(levelname)s %(name)s: %(message)s")
    file_handler = WatchedFileHandler(logfile, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logging.basicConfig(
        level=getattr(logging, config.level),
        handlers=[file_handler, stream_handler],
        force=True,
    )
