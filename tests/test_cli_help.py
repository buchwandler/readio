from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import cli


def test_main_without_arguments_prints_help_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "Usage:" in captured.out
    assert "Commands" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize("argv", [["-h"], ["--help"]])
def test_explicit_root_help_exits_zero(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "Commands" in captured.out
    assert "error:" not in captured.err


def test_file_input_help_describes_conversion(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["speak", "--help"])

    assert exc.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "read or convert a document file" in help_text
    assert "auto uses ssmdconvert for file inputs" in help_text
    assert "explicit text/markdown/ssmd selects the input format" in help_text


def test_root_help_uses_backend_neutral_description_and_lists_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    help_text = " ".join(capsys.readouterr().out.split())
    assert "Plan, synthesize, compose, and export speech and audiobooks" in help_text
    assert "multiple TTS engines" in help_text
    assert "Stream text to PyKokoro TTS" not in help_text
    assert "[OPTIONS] COMMAND [ARGS]..." in help_text
    for command in (
        "speak",
        "render",
        "project",
        "plan",
        "audiobook",
        "voices",
        "models",
        "engines",
        "formats",
        "doctor",
    ):
        assert command in help_text


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        (["not-a-command"], "invalid choice"),
        (["render", "--definitely-not-an-option"], "unrecognized arguments"),
    ],
)
def test_invalid_command_or_option_remains_parser_error(
    argv: list[str], error: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)

    captured = capsys.readouterr()
    assert exc.value.code == 2
    assert error in captured.err
    assert captured.out == ""


@pytest.mark.parametrize(
    ("command", "child"),
    [
        ("project", "init"),
        ("audiobook", "chapters"),
        ("spotify", "publish"),
        ("models", "list"),
        ("lexicons", "list"),
        ("defaults", "list"),
        ("voices", "list"),
        ("roles", "list"),
        ("ssmd", "bind"),
        ("config", "path"),
        ("template", "list"),
        ("ingest", "path"),
    ],
)
def test_command_group_without_child_prints_group_help(
    command: str, child: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([command])

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert child in captured.out
    assert "error:" not in captured.err


def test_plan_without_subcommand_still_builds_current_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[Path] = []
    result = SimpleNamespace(scopes=(), to_dict=dict)

    def plan(path: Path) -> SimpleNamespace:
        calls.append(path)
        return result

    app = SimpleNamespace(projects=SimpleNamespace(plan=plan))
    monkeypatch.setattr(cli, "_api_for", lambda _args: app)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc:
        cli.main(["plan"])

    assert exc.value.code == 0
    assert calls == [tmp_path]
    assert "Semantic plans: 0 scopes" in capsys.readouterr().out


def test_engines_command_lists_public_catalog_statuses_and_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    engines = [
        SimpleNamespace(
            id="pykokoro",
            version="0.10.0",
            registered=True,
            installed=True,
            runnable=True,
            missing_dependency=None,
            to_dict=lambda: {"id": "pykokoro", "runnable": True},
        ),
        SimpleNamespace(
            id="pocket",
            version=None,
            registered=True,
            installed=False,
            runnable=False,
            missing_dependency="pocketsynth",
            to_dict=lambda: {"id": "pocket", "installed": False},
        ),
        SimpleNamespace(
            id="other",
            version=None,
            registered=False,
            installed=True,
            runnable=False,
            missing_dependency=None,
            to_dict=lambda: {"id": "other", "registered": False},
        ),
    ]
    calls = []

    def get_engines():
        calls.append("engines")
        return engines

    app = SimpleNamespace(catalog=SimpleNamespace(engines=get_engines))
    monkeypatch.setattr(cli, "_api_for", lambda _args: app)

    with pytest.raises(SystemExit) as exc:
        cli.main(["engines"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "pykokoro" in output and "ready" in output
    assert "pocket" in output and "missing_dependency" in output
    assert "pocketsynth" in output
    assert "other" in output and "unavailable" in output

    with pytest.raises(SystemExit) as exc:
        cli.main(["engines", "--json"])
    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "engines": [
            {"id": "pykokoro", "runnable": True},
            {"id": "pocket", "installed": False},
            {"id": "other", "registered": False},
        ],
    }
    assert calls == ["engines", "engines"]


def test_formats_command_uses_public_catalog_and_audiobook_formats(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    audio_formats = [
        SimpleNamespace(
            id="wav",
            suffix=".wav",
            available=True,
            reason=None,
            to_dict=lambda: {"id": "wav", "suffix": ".wav", "available": True, "reason": None},
        ),
        SimpleNamespace(
            id="mp3",
            suffix=".mp3",
            available=False,
            reason="FFmpeg executable not found",
            to_dict=lambda: {
                "id": "mp3",
                "suffix": ".mp3",
                "available": False,
                "reason": "FFmpeg executable not found",
            },
        ),
    ]
    calls = []

    def get_audio_formats():
        calls.append("audio_formats")
        return audio_formats

    app = SimpleNamespace(catalog=SimpleNamespace(audio_formats=get_audio_formats))
    monkeypatch.setattr(cli, "_api_for", lambda _args: app)

    with pytest.raises(SystemExit) as exc:
        cli.main(["formats"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "Generic audio formats" in output
    assert "wav" in output and "available" in output
    assert "FFmpeg executable not found" in output
    assert "Audiobook formats" in output
    assert "m4b" in output and ".m4b" in output

    with pytest.raises(SystemExit) as exc:
        cli.main(["formats", "--json"])
    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "audio": [
            {"id": "wav", "suffix": ".wav", "available": True, "reason": None},
            {
                "id": "mp3",
                "suffix": ".mp3",
                "available": False,
                "reason": "FFmpeg executable not found",
            },
        ],
        "audiobook": [{"id": "m4b", "suffix": ".m4b"}],
    }
    assert calls == ["audio_formats", "audio_formats"]
