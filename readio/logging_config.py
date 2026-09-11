from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import TextIO

MAX_VERBOSITY = 2
_MANAGED_HANDLER = "_readio_verbose_handler"
_LOGGER_NAMES = ("readio", "pykokoro")


class IsoLocalFormatter(logging.Formatter):
    """Format records with local timezone-aware ISO-8601 timestamps."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return (
            datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds")
        )


def logging_level_for_verbosity(verbosity: int) -> int:
    """Map the CLI verbosity count to the supported logging level."""
    if verbosity <= 0:
        return logging.NOTSET
    if verbosity == 1:
        return logging.INFO
    return logging.DEBUG


def configure_logging(verbosity: int, *, stream: TextIO | None = None) -> None:
    if verbosity <= 0:
        return

    stream = sys.stderr if stream is None else stream
    level = logging_level_for_verbosity(verbosity)
    formatter = IsoLocalFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    setattr(handler, _MANAGED_HANDLER, True)

    for name in _LOGGER_NAMES:
        logger = logging.getLogger(name)
        for existing in logger.handlers[:]:
            if getattr(existing, _MANAGED_HANDLER, False):
                logger.removeHandler(existing)
                existing.close()
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
