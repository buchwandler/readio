"""Global CLI diagnostics and monotonic timing helpers."""

from __future__ import annotations

import logging
import shlex
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

LOGGER_NAME = "readio"


class _ISO8601Formatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        del datefmt
        return (
            datetime.fromtimestamp(record.created, tz=timezone.utc)
            .astimezone()
            .isoformat(timespec="milliseconds")
        )


def _field_value(value: object) -> str:
    if isinstance(value, Path) or value is None or isinstance(value, (str, int, float, bool)):
        rendered = str(value)
    else:
        # Avoid leaking arbitrarily large or sensitive native objects in diagnostics.
        rendered = f"<{type(value).__name__}>"
    return shlex.quote(rendered)


def _field_text(fields: dict[str, object]) -> str:
    return " ".join(f"{name}={_field_value(value)}" for name, value in fields.items())


def configure_diagnostics(verbosity: int) -> None:
    """Configure Readio's logger; verbosity 0 preserves the quiet default."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.propagate = False
    if verbosity <= 0:
        logger.setLevel(logging.CRITICAL + 1)
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_ISO8601Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbosity >= 2 else logging.INFO)


def logger_for(component: str) -> logging.Logger:
    """Return a logger below the Readio diagnostics namespace."""
    return logging.getLogger(f"{LOGGER_NAME}.{component}")


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: object,
) -> None:
    """Emit one compact event with scalar, shell-quoted fields."""
    suffix = _field_text(fields)
    logger.log(level, "%s%s", event, f" {suffix}" if suffix else "")


@contextmanager
def timed_span(
    logger: logging.Logger,
    name: str,
    *,
    level: int = logging.INFO,
    **fields: object,
) -> Iterator[None]:
    """Log a span's start and monotonic elapsed time, including failed spans."""
    log_event(logger, level, f"{name}.start", **fields)
    start = time.perf_counter()
    try:
        yield
    except BaseException as error:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        failure_fields = dict(fields)
        failure_fields["elapsed_ms"] = round(elapsed_ms, 3)
        failure_fields["error_type"] = type(error).__name__
        stage = getattr(error, "stage", None)
        if stage is not None:
            failure_fields["stage"] = stage
        log_event(logger, logging.ERROR, f"{name}.failed", **failure_fields)
        raise
    else:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        end_fields = dict(fields)
        end_fields["elapsed_ms"] = round(elapsed_ms, 3)
        log_event(logger, level, f"{name}.end", **end_fields)


__all__ = ["configure_diagnostics", "log_event", "logger_for", "timed_span"]
