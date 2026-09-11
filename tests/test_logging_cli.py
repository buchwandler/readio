from __future__ import annotations

import io
import json
import logging
import re
from argparse import Namespace

import pytest

from readio import cli
from readio.logging_config import configure_logging, logging_level_for_verbosity


@pytest.fixture(autouse=True)
def clean_verbose_logging():
    _reset_verbose_loggers()
    yield
    _reset_verbose_loggers()


def _reset_verbose_loggers() -> None:
    for name in ("readio", "pykokoro"):
        logger = logging.getLogger(name)
        for handler in logger.handlers[:]:
            if getattr(handler, "_readio_verbose_handler", False):
                logger.removeHandler(handler)
                handler.close()
        logger.setLevel(logging.NOTSET)
        logger.propagate = True


def test_logging_level_mapping_and_timestamp_format():
    assert logging_level_for_verbosity(0) == logging.NOTSET
    assert logging_level_for_verbosity(1) == logging.INFO
    assert logging_level_for_verbosity(2) == logging.DEBUG
    assert logging_level_for_verbosity(3) == logging.DEBUG

    stream = io.StringIO()
    configure_logging(1, stream=stream)
    logger = logging.getLogger("readio.test")
    logger.info("lifecycle")
    logger.debug("hidden")

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert re.match(
        r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*[+-][0-9]{2}:[0-9]{2} INFO readio\.test lifecycle$",
        lines[0],
    )


def test_debug_logging_and_pykokoro_namespace_propagate():
    stream = io.StringIO()
    configure_logging(2, stream=stream)
    logging.getLogger("pykokoro.test").debug("internal")
    assert "DEBUG pykokoro.test internal" in stream.getvalue()


def test_repeated_configuration_does_not_duplicate_records():
    stream = io.StringIO()
    configure_logging(1, stream=stream)
    configure_logging(1, stream=stream)
    logging.getLogger("readio.test").info("once")
    assert stream.getvalue().count(" once") == 1


def test_global_options_are_extracted_and_literal_options_are_preserved():
    normalized, options = cli._extract_global_options(
        ["-vvv", "speak", "test", "--verbose", "--json"]
    )
    assert normalized == ["speak", "test"]
    assert options.verbosity == 2
    assert options.json is True

    normalized, options = cli._extract_global_options(
        ["speak", "--", "--verbose", "-vv", "--json"]
    )
    assert normalized == ["speak", "--", "--verbose", "-vv", "--json"]
    assert options.verbosity == 0
    assert options.json is False


def test_parser_exposes_top_level_verbose_option():
    args = cli.build_parser().parse_args(["-v", "doctor"])
    assert args.verbose == 1


def test_verbose_logs_use_stderr_and_keep_json_on_stdout(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_cmd_doctor", lambda args: print('{"ok": true}') or 0)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["-v", "doctor", "--json"])

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"ok": True}
    assert "INFO readio.cli command.start command=doctor" in captured.err
    assert "command.finish command=doctor" in captured.err
    assert "T" not in captured.out


def test_debug_error_keeps_json_error_and_adds_traceback(monkeypatch, capsys):
    def fail(args):
        raise ValueError("bad input")

    monkeypatch.setattr(cli, "_cmd_doctor", fail)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["-vv", "doctor", "--json"])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"] == "bad input"
    assert "DEBUG readio.cli command.error" in captured.err
    assert "Traceback" in captured.err


def test_verbose_progress_is_line_oriented_on_tty(monkeypatch):
    class TtyStream:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(cli.sys, "stderr", TtyStream())
    progress = cli._build_progress(Namespace(verbose=1, progress=None, json=False))
    assert progress.enabled is True
    assert progress._tty is False


def test_no_progress_remains_independent_from_verbose():
    args = Namespace(verbose=2, progress=False, json=False)
    assert cli.progress_enabled(args, io.StringIO()) is False


def test_quiet_configuration_does_not_emit_new_records():
    stream = io.StringIO()
    configure_logging(0, stream=stream)
    logging.getLogger("readio.test").info("quiet")
    assert stream.getvalue() == ""
