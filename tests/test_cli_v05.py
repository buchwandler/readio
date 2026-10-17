from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import cli
from readio.book import BookBuildResult


def test_parser_exposes_only_the_retained_command_families() -> None:
    help_text = cli.build_parser().format_help()
    assert "speak" in help_text
    assert "audiobook" in help_text
    assert "doctor" in help_text
    for removed in ("plan", "project", "synth", "compose", "export", "spotify"):
        assert removed not in help_text


def test_speak_command_passes_text_and_voice_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli.api, "speak", lambda text, **kwargs: captured.update(text=text, **kwargs)
    )

    assert (
        cli.main(
            [
                "speak",
                "Hello,",
                "world!",
                "--voice",
                "inflect:micro-v2/default",
                "--lang",
                "en",
                "--engine-option",
                "temperature=0.7",
            ]
        )
        == 0
    )

    assert captured == {
        "text": "Hello, world!",
        "voice": "inflect:micro-v2/default",
        "engine": None,
        "target": None,
        "language": "en",
        "speaker": None,
        "offline": False,
        "refresh": False,
        "engine_options": {"temperature": "0.7"},
        "profile": None,
        "role": None,
        "output": None,
        "no_play": False,
    }


def test_convert_command_calls_public_api_and_prints_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "novel.epub"
    workspace = tmp_path / "novel.ssmdbook"
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli.api,
        "convert_book",
        lambda *args, **kwargs: (
            captured.update(args=args, kwargs=kwargs) or SimpleNamespace(path=workspace)
        ),
    )

    assert (
        cli.main(["audiobook", "convert", str(source), "--chapters", "5-17", "--no-progress"]) == 0
    )
    assert captured["args"] == (source, None)
    assert captured["kwargs"] == {
        "chapters": "5-17",
        "language": None,
        "metadata": {},
        "sequence_fallback_mode": None,
        "speech_mode": None,
        "spacy": None,
        "on_progress": None,
    }
    assert str(workspace) in capsys.readouterr().out


def test_build_command_constructs_request_and_reports_actual_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "book.ssmdbook"
    profile = tmp_path / "narration.renderprofile.toml"
    result = BookBuildResult(
        workspace=workspace,
        variant="narration--exact",
        compile_result=object(),
        renderplan_result=object(),
        voicerender_result=object(),
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli.api,
        "build_book",
        lambda request, **kwargs: captured.update(request=request, kwargs=kwargs) or result,
    )

    assert (
        cli.main(
            [
                "audiobook",
                "build",
                str(workspace),
                "--profile",
                str(profile),
                "--stop-after",
                "synth",
                "--no-progress",
            ]
        )
        == 0
    )

    request = captured["request"]
    assert request.workspace == workspace
    assert request.profile == profile
    assert request.stop_after == "synth"
    assert captured["kwargs"] == {"on_progress": None}
    output = capsys.readouterr().out
    assert "Variant: narration--exact" in output
    assert "Completed: compile, render-plan, synth" in output


def test_doctor_json_uses_public_health_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = {"readio": "0.5.0", "packages": {}, "engines": []}
    monkeypatch.setattr(cli, "doctor_report", lambda: report)

    assert cli.main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == report


def test_invalid_engine_option_returns_cli_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["speak", "Hello", "--engine-option", "bad-option"]) == 2
    assert "KEY=VALUE" in capsys.readouterr().err


def test_removed_commands_are_unknown() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["render"])


def test_removed_audiobook_aliases_are_unknown() -> None:
    parser = cli.build_parser()
    for command in ("chapters", "init", "compile", "render-plan", "synth", "compose", "export"):
        with pytest.raises(SystemExit):
            parser.parse_args(["audiobook", command])


def test_public_api_exports_control_plane_operations() -> None:
    from readio import api

    assert set(api.__all__) == {
        "BookBuildRequest",
        "BookBuildResult",
        "BookProgressEvent",
        "CONFIG_TEMPLATE",
        "DocumentRenderRequest",
        "DocumentRenderResult",
        "DocumentStopAfter",
        "ProgressEvent",
        "ProgressHandler",
        "Qwen3VoiceDesignRecipe",
        "VoiceSelector",
        "build_book",
        "clone_voice",
        "convert_book",
        "create_profile",
        "create_voice_source",
        "design_voice",
        "init_settings",
        "inspect_engine",
        "inspect_engine_model",
        "inspect_voice",
        "install_engine_model",
        "list_engine_models",
        "list_engine_options",
        "list_engines",
        "list_profile_roles",
        "list_voices",
        "load_profile",
        "load_settings",
        "merge_settings",
        "open_book",
        "parse_voice_selector",
        "refresh_profile",
        "remove_engine_model",
        "remove_profile_role",
        "render_document",
        "set_default_profile_role",
        "set_profile_role",
        "show_voice_source",
        "speak",
        "validate_profile",
        "validate_settings",
        "verify_engine_model",
        "verify_voice_source",
    }
    assert not hasattr(api, "Readio")
    assert not hasattr(api, "create_project")
