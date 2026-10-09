import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import cli
from readio.api import PlanNotExecutableError, Readio, ResolvedPlan
from readio.api.configuration import ConfigurationService
from readio.api.errors import PlannedOutputError
from readio.api.events import ReadioEvent
from readio.api.projects import ProjectService
from readio.api.speech import SpeechService
from readio.api.types import (
    CatalogDiscovery,
    CatalogListing,
    ConfigurationInitResult,
    Diagnostic,
    LoudnessSummary,
    NextAction,
    ProjectCompositionResult,
    ProjectRef,
    ProjectStatus,
    RenderResult,
    StageStatus,
    VoiceInfo,
)
from readio.audio import RenderSummary
from readio.cli import _validate_live, build_parser
from readio.config import PathSettings, ReadioConfig


def _install_render_stub(monkeypatch, callback):
    def plan(self, request):
        output = request.output.requested_path
        if output is None:
            source = request.input.document.source_path
            stem = source.stem if source is not None else "speech"
            output = self._app.config.paths.output / f"{stem}.wav"
        suffix_format = output.suffix.lstrip(".").lower() or "wav"
        audio_format = request.output.requested_format or suffix_format
        conflict = bool(
            request.output.requested_format and suffix_format != request.output.requested_format
        )
        return SimpleNamespace(
            ok=not conflict,
            output=SimpleNamespace(
                path=output,
                format=audio_format,
                force=request.output.force,
            ),
            to_dict=lambda: {
                "ok": not conflict,
                "diagnostics": [
                    {
                        "code": "output_format_conflict",
                        "message": "requested audio format conflicts with output extension",
                    }
                ]
                if conflict
                else [],
            },
        )

    def render(self, request, *, on_event=None, write_manifest=False):
        resolved = self.plan(request)
        output = resolved.output.path
        assert output is not None
        if not resolved.ok:
            raise PlannedOutputError(
                "render plan is invalid",
                code="output.format_conflict",
                plan=resolved,
                diagnostics=(
                    Diagnostic(
                        code="output_format_conflict",
                        severity="error",
                        message="requested audio format conflicts with output extension",
                    ),
                ),
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        summary, manifest_path = callback(request, resolved, output, on_event, write_manifest)
        return RenderResult(
            plan=resolved,
            summary=summary,
            output_path=output,
            manifest_path=manifest_path,
            audio_format=resolved.output.format,
            manifest_schema=("readio.render-manifest.v2" if manifest_path is not None else None),
        )

    monkeypatch.setattr(SpeechService, "plan", plan)
    monkeypatch.setattr(SpeechService, "render", render)


def test_render_cli_uses_plan_attached_to_typed_failure(monkeypatch, capsys) -> None:
    app = Readio(ReadioConfig())
    error = PlanNotExecutableError(
        "speech request cannot be executed",
        plan=ResolvedPlan(),
        diagnostics=(),
    )

    def fail_render(self, _request, **_kwargs):
        raise error

    monkeypatch.setattr(cli, "_api_for", lambda _args: app)
    monkeypatch.setattr(SpeechService, "render", fail_render)
    monkeypatch.setattr(
        SpeechService,
        "plan",
        lambda *_args, **_kwargs: pytest.fail("render CLI replanned after execution error"),
    )
    monkeypatch.setattr(cli, "format_plan_human", lambda _plan: "attached plan")
    args = build_parser().parse_args(["render", "Hello world", "--no-progress"])

    assert cli._cmd_render(args) == 1
    assert capsys.readouterr().out == "attached plan\n"


def test_single_existing_positional_ssmd_is_normalized_to_file(tmp_path: Path):
    source = tmp_path / "episode.ssmd"
    source.write_text("---\nssmd_version: '0.9'\n---\nHello.\n", encoding="utf-8")
    args = build_parser().parse_args(["render", str(source), "-o", str(tmp_path / "out.mp3")])

    cli._normalize_positional_input(args)

    assert args.file == source
    assert args.text == []
    document = cli._read_input(args, ReadioConfig())
    assert document.source_path == source
    assert document.format == "ssmd"
    assert "Hello." in document.text


def test_existing_positional_markdown_uses_markdown_format(tmp_path: Path):
    source = tmp_path / "notes.md"
    source.write_text("# Heading", encoding="utf-8")
    args = build_parser().parse_args(["speak", str(source)])

    cli._normalize_positional_input(args)

    document = cli._read_input(args, ReadioConfig())
    assert document.source_path == source
    assert document.format == "ssmd"
    assert "Heading" in document.text


def test_existing_positional_txt_is_loaded_as_file(tmp_path: Path):
    source = tmp_path / "notes.txt"
    source.write_text("hello", encoding="utf-8")
    args = build_parser().parse_args(["speak", str(source)])

    cli._normalize_positional_input(args)

    document = cli._read_input(args, ReadioConfig())
    assert document.format == "ssmd"
    assert "hello" in document.text
    assert document.source_path == source


@pytest.mark.parametrize(
    "suffix",
    (".text", ".html", ".htm", ".xhtml", ".pdf", ".docx", ".epub"),
)
def test_supported_document_suffixes_are_detected_as_files(tmp_path: Path, suffix: str):
    source = tmp_path / f"document{suffix}"
    source.write_text("source", encoding="utf-8")
    args = build_parser().parse_args(["render", str(source)])

    cli._normalize_positional_input(args)

    assert args.file == source
    assert args.text == []


def test_ssmdbook_suffix_is_not_a_generic_document_suffix(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["render", "book.ssmdbook"])

    cli._normalize_positional_input(args)

    assert args.file is None
    assert args.text == ["book.ssmdbook"]


def test_explicit_text_format_keeps_existing_filename_literal(tmp_path: Path):
    source = tmp_path / "README.md"
    source.write_text("# Not spoken", encoding="utf-8")
    args = build_parser().parse_args(["speak", "--input-format", "text", str(source)])

    cli._normalize_positional_input(args)

    assert args.file is None
    assert args.text == [str(source)]
    assert cli._read_input(args, ReadioConfig()).text == str(source)


def test_missing_positional_ssmd_path_fails_instead_of_being_spoken(tmp_path: Path):
    source = tmp_path / "missing.ssmd"
    args = build_parser().parse_args(["render", str(source)])

    with pytest.raises(ValueError, match="looks like a file path"):
        cli._normalize_positional_input(args)


def test_missing_pathlike_token_with_separator_fails(tmp_path: Path):
    source = tmp_path / "missing" / "episode"
    args = build_parser().parse_args(["speak", str(source)])

    with pytest.raises(ValueError, match="looks like a file path"):
        cli._normalize_positional_input(args)


def test_existing_positional_directory_is_rejected(tmp_path: Path):
    args = build_parser().parse_args(["speak", str(tmp_path)])

    with pytest.raises(ValueError, match="not a regular file"):
        cli._normalize_positional_input(args)


def test_file_and_positional_input_are_rejected():
    args = build_parser().parse_args(["render", "literal", "--file", "episode.ssmd"])

    with pytest.raises(ValueError, match="either positional"):
        cli._normalize_positional_input(args)
    with pytest.raises(ValueError, match="either positional"):
        cli._read_input(args, ReadioConfig())


def test_multiple_positional_tokens_remain_literal_text():
    args = build_parser().parse_args(["speak", "release", "notes"])

    cli._normalize_positional_input(args)

    assert args.file is None
    assert args.text == ["release", "notes"]


def test_single_positional_path_with_spaces_and_symlink_is_loaded(tmp_path: Path):
    source = tmp_path / "weekly review.ssmd"
    source.write_text("---\nssmd_version: '0.9'\n---\nHello.\n", encoding="utf-8")
    link = tmp_path / "linked review.ssmd"
    link.symlink_to(source)
    args = build_parser().parse_args(["speak", str(link)])

    cli._normalize_positional_input(args)

    assert args.file == link
    assert cli._read_input(args, ReadioConfig()).source_path == source.resolve()


def test_positional_ssmd_is_passed_to_the_public_speech_service(monkeypatch, tmp_path: Path):
    source = tmp_path / "episode.ssmd"
    body = "---\nssmd_version: '0.9'\n---\nFile body.\n"
    source.write_text(body, encoding="utf-8")
    args = build_parser().parse_args(["speak", str(source)])
    captured = []

    def speak(self, request, *, on_event=None):
        captured.append(request)

    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())
    monkeypatch.setattr(SpeechService, "speak", speak)

    assert cli._cmd_speak(args) == 0
    document = captured[0].input.document
    assert document.format == "ssmd"
    assert document.source_path == source
    assert "File body." in document.text


def test_render_resolves_output_with_normalized_positional_path(monkeypatch, tmp_path: Path):
    source = tmp_path / "episode.ssmd"
    source.write_text("---\nssmd_version: '0.9'\n---\nHello.\n", encoding="utf-8")
    cfg = ReadioConfig(paths=PathSettings(output=tmp_path / "output"))
    captured = []

    def render(request, plan, output, _on_event, _write_manifest):
        captured.append((request.input.document.source_path, output))
        output.write_bytes(b"wav")
        return RenderSummary(sample_rate=24000, sample_count=24000, channels=1), None

    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)
    _install_render_stub(monkeypatch, render)
    args = build_parser().parse_args(["render", str(source), "--no-progress"])

    assert cli._cmd_render(args) == 0
    assert captured[0][0] == source
    assert captured[0][1].parent == tmp_path / "output"
    assert captured[0][1].name == "episode.wav"


def test_missing_positional_path_fails_before_synthesis(monkeypatch, tmp_path: Path):
    args = build_parser().parse_args(["render", str(tmp_path / "missing.ssmd")])
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())
    monkeypatch.setattr(
        SpeechService,
        "plan",
        lambda *_args, **_kwargs: pytest.fail("speech was planned"),
    )

    with pytest.raises(ValueError, match="looks like a file path"):
        cli._cmd_render(args)


def test_input_help_describes_positional_files_and_literal_escape(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["speak", "--help"])

    help_text = capsys.readouterr().out
    assert "one existing file path" in help_text
    assert "unambiguous scripting form" in help_text
    assert "selects the input format" in help_text
    assert "positional file detection" in help_text
    assert "inflect:nano-v2/default" in help_text


@pytest.mark.parametrize("command", ["speak", "render"])
def test_pause_mode_cli_is_unset_when_omitted(command):
    args = build_parser().parse_args([command, "hello"])
    assert args.pause_mode is None


def test_voice_level_cli_option_is_available_on_render() -> None:
    args = build_parser().parse_args(
        ["render", "hello", "--speed", "1.25", "--voice-level", "calibrated"]
    )
    assert args.speed == 1.25
    assert args.voice_level == "calibrated"


def test_render_parser_has_shared_input_and_output_options():
    args = build_parser().parse_args(
        ["render", "literal", "--file", "episode.ssmd", "--select", "paragraph:2", "-o", "out.wav"]
    )
    assert args.command == "render"
    assert args.text == ["literal"]
    assert args.file == Path("episode.ssmd")
    assert args.select == "paragraph:2"
    assert args.output == Path("out.wav")


def test_render_and_spotify_parse_audio_format():
    render = build_parser().parse_args(["render", "text", "--format", "mp3"])
    spotify = build_parser().parse_args(
        ["spotify", "publish", "text", "--title", "Episode", "--format", "m4a"]
    )
    assert render.format == "mp3"
    assert spotify.format == "m4a"


def test_invalid_audio_format_is_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["render", "text", "--format", "m4b"])


def test_live_rejects_file_and_selection():
    args = build_parser().parse_args(["render", "--live", "--file", "input.txt", "-o", "out.wav"])
    with pytest.raises(ValueError, match="stdin only"):
        _validate_live(args)

    args = build_parser().parse_args(
        ["render", "--live", "--select", "paragraph:2", "-o", "out.wav"]
    )
    with pytest.raises(ValueError, match="not available"):
        _validate_live(args)


def test_input_format_option_and_live_markdown_restriction():
    args = build_parser().parse_args(["speak", "--input-format", "markdown", "#", "Heading"])
    assert args.input_format == "markdown"

    live = build_parser().parse_args(["speak", "--live", "--input-format", "markdown"])
    with pytest.raises(ValueError, match="complete-document parsing"):
        _validate_live(live)


def test_piper_live_mode_is_supported_by_the_released_text_request_api():
    from readio.engines.pipersynth import PiperSynthEngineAdapter

    assert PiperSynthEngineAdapter().capabilities().supports_live


def test_live_render_delegates_file_ownership_and_reports_service_metadata(
    monkeypatch, capsys, tmp_path: Path
):
    target = tmp_path / "live.wav"
    cfg = ReadioConfig(paths=PathSettings(output=tmp_path / "output"))
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)
    monkeypatch.setattr(cli.sys, "stdin", StringIO("Hello live\n"))
    calls = []

    def render_live_to_file(_self, lines, output, *, synthesis=None, unit=None, on_event=None):
        target.write_bytes(b"wav")
        calls.append((lines, output, synthesis, unit, on_event))
        return RenderResult(
            plan=None,
            summary=RenderSummary(sample_rate=24000, sample_count=24000, channels=1),
            output_path=target,
            audio_format="wav",
        )

    monkeypatch.setattr(SpeechService, "render_live_to_file", render_live_to_file)
    args = build_parser().parse_args(["render", "--live", "--json", "--force", "-o", str(target)])

    assert cli._cmd_render(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert calls[0][1].requested_path == target
    assert calls[0][1].force is True
    assert payload["path"] == str(target)
    assert payload["format"] == "wav"
    assert payload["manifest"] is None


def test_synthesis_parser_exposes_speaker_and_asset_policy():
    args = build_parser().parse_args(
        [
            "render",
            "hello",
            "--speaker",
            "narrator",
            "--offline",
            "--refresh",
        ]
    )
    assert args.speaker == "narrator"
    assert args.offline is True
    assert args.refresh is True


def test_pocketsynth_cli_options_lower_to_public_synthesis_request(tmp_path: Path):
    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"reference")
    args = build_parser().parse_args(
        [
            "render",
            "hello",
            "--engine",
            "pocket",
            "--voice-file",
            str(reference),
            "--precision",
            "fp32",
            "--temperature",
            "0.6",
            "--lsd-steps",
            "3",
            "--max-frames",
            "120",
            "--frames-after-eos",
            "4",
        ]
    )

    synthesis = cli._synthesis_request_from_args(args)

    assert synthesis.engine == "pocket"
    assert synthesis.voice_file == reference
    assert synthesis.engine_options == {
        "precision": "fp32",
        "temperature": 0.6,
        "lsd_steps": 3,
        "max_frames": 120,
        "frames_after_eos": 4,
    }


@pytest.mark.parametrize(
    "selectors",
    [
        ("--voice", "af_sarah", "--voice-file", "voice.wav"),
        ("--voice", "af_sarah", "--voice-prompt", "kyutai-tts-voices:alba/casual"),
        ("--voice-file", "voice.wav", "--voice-prompt", "kyutai-tts-voices:alba/casual"),
        (
            "--voice",
            "af_sarah",
            "--voice-file",
            "voice.wav",
            "--voice-prompt",
            "kyutai-tts-voices:alba/casual",
        ),
    ],
)
def test_voice_selectors_are_mutually_exclusive_in_cli(selectors):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["render", "hello", *selectors])


def test_managed_prompt_cli_option_lowers_to_public_synthesis_request():
    args = build_parser().parse_args(
        ["render", "hello", "--engine", "pocket", "--voice-prompt", "kyutai-tts-voices:alba/casual"]
    )
    synthesis = cli._synthesis_request_from_args(args)
    assert synthesis.engine == "pocket"
    assert synthesis.voice_prompt == "kyutai-tts-voices:alba/casual"


def test_project_settings_cli_accepts_managed_prompt_and_excludes_other_voice_sources():
    prompt = "kyutai-tts-voices:alba-mackenna/casual"
    args = build_parser().parse_args(
        ["project", "settings", "set", "--engine", "pocket", "--voice-prompt", prompt]
    )
    assert args.voice_prompt == prompt

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["project", "settings", "set", "--voice", "named", "--voice-prompt", prompt]
        )


def test_speak_uses_the_public_speech_service_without_creating_a_project(monkeypatch, tmp_path):
    captured = []
    config = ReadioConfig(
        paths=PathSettings(output=tmp_path / "output"),
    )
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: config)
    monkeypatch.setattr(
        SpeechService,
        "speak",
        lambda _self, request, *, on_event=None: captured.append(request),
    )

    args = build_parser().parse_args(["speak", "hello"])
    assert cli._cmd_speak(args) == 0
    assert captured[0].operation == "speak"
    assert captured[0].input.document.text == "hello"
    assert tuple(tmp_path.iterdir()) == ()


def test_render_uses_selected_format_and_output_suffix(monkeypatch, tmp_path: Path):
    cfg = ReadioConfig(paths=PathSettings(output=tmp_path / "output"))
    calls = []
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)

    def render(_request, plan, path, _on_event, _write_manifest):
        calls.append((path, plan.output.format))
        path.write_bytes(b"mp3")
        return RenderSummary(sample_rate=24000, sample_count=24000, channels=1), None

    _install_render_stub(monkeypatch, render)
    output = tmp_path / "episode.mp3"
    args = build_parser().parse_args(["render", "text", "-o", str(output)])
    assert cli._cmd_render(args) == 0
    assert output.read_bytes() == b"mp3"
    assert calls[0][1] == "mp3"
    assert calls[0][0].suffix == ".mp3"


def test_render_rejects_format_conflict_before_tts_load(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())

    def fail(*_args, **_kwargs):
        pytest.fail("render was started")

    _install_render_stub(monkeypatch, fail)
    args = build_parser().parse_args(
        ["render", "text", "--format", "mp3", "-o", str(tmp_path / "episode.ogg"), "--json"]
    )
    assert cli._cmd_render(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["diagnostics"][0]["code"] == "output_format_conflict"


def test_render_progress_flags_and_defaults():
    parser = build_parser()
    assert parser.parse_args(["render", "text"]).progress is None
    assert parser.parse_args(["render", "text", "--progress"]).progress is True
    assert parser.parse_args(["render", "text", "--no-progress"]).progress is False
    assert parser.parse_args(["spotify", "publish", "text", "--title", "Episode"]).progress is None


def test_progress_enablement_respects_tty_and_json():
    class Stream:
        def __init__(self, tty: bool):
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    auto = build_parser().parse_args(["render", "text"])
    assert cli.progress_enabled(auto, Stream(True))
    assert not cli.progress_enabled(auto, Stream(False))

    json_args = build_parser().parse_args(
        ["spotify", "publish", "text", "--title", "Episode", "--json"]
    )
    assert not cli.progress_enabled(json_args, Stream(True))
    explicit = build_parser().parse_args(
        ["spotify", "publish", "text", "--title", "Episode", "--json", "--progress"]
    )
    assert cli.progress_enabled(explicit, Stream(False))


def test_forced_render_progress_uses_stderr_and_keeps_path_on_stdout(
    monkeypatch, capsys, tmp_path: Path
):
    cfg = ReadioConfig(paths=PathSettings(output=tmp_path / "output"))
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)

    def render(_request, _plan, path, on_event, _write_manifest):
        path.write_bytes(b"wav")
        if on_event is not None:
            on_event(
                ReadioEvent(
                    kind="stage.started",
                    operation="render",
                    stage="synthesis",
                    message="Finalizing WAV",
                )
            )
        return RenderSummary(sample_rate=24000, sample_count=24000, channels=1), None

    _install_render_stub(monkeypatch, render)
    output = tmp_path / "episode.wav"
    args = build_parser().parse_args(["render", "text", "--progress", "-o", str(output)])

    assert cli._cmd_render(args) == 0
    captured = capsys.readouterr()
    assert captured.out == f"{output}\n"
    assert "Planning" in captured.err
    assert "Finalizing" in captured.err
    assert "Rendered 0 units" in captured.err


def test_config_init_delegates_initialization_and_preserves_printed_path(
    monkeypatch, capsys, tmp_path: Path
):
    config_path = tmp_path / "config.toml"
    result = ConfigurationInitResult(path=config_path, created_directories=())
    calls = []
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())
    monkeypatch.setattr(
        ConfigurationService,
        "initialize",
        lambda _self, *, overwrite=False: calls.append(overwrite) or result,
    )
    args = build_parser().parse_args(["config", "init", "--force"])

    assert cli._cmd_config(args) == 0
    assert calls == [True]
    assert capsys.readouterr().out == f"{config_path}\n"


def test_no_progress_suppresses_render_status(monkeypatch, capsys, tmp_path: Path):
    cfg = ReadioConfig(paths=PathSettings(output=tmp_path / "output"))
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)

    def render(_request, _plan, path, _on_event, _write_manifest):
        path.write_bytes(b"wav")
        return RenderSummary(sample_rate=24000, sample_count=24000, channels=1), None

    _install_render_stub(monkeypatch, render)
    output = tmp_path / "episode.wav"
    args = build_parser().parse_args(["render", "text", "--no-progress", "-o", str(output)])

    assert cli._cmd_render(args) == 0
    assert capsys.readouterr().err == ""


def test_render_json_reports_stable_envelope(monkeypatch, capsys, tmp_path: Path):
    cfg = ReadioConfig(paths=PathSettings(output=tmp_path / "output"))
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)

    def render(_request, _plan, path, _on_event, _write_manifest):
        path.write_bytes(b"wav")
        return RenderSummary(sample_rate=24000, sample_count=24000, channels=1), None

    _install_render_stub(monkeypatch, render)
    output = tmp_path / "episode.wav"
    args = build_parser().parse_args(["render", "text", "--json", "-o", str(output)])

    assert cli._cmd_render(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["duration_ms"] == 1000
    assert result["path"] == str(output)


def test_project_commands_share_progress_option():
    synth = build_parser().parse_args(["synth", "--progress"])
    compose = build_parser().parse_args(["compose", "--progress"])
    compose_disabled = build_parser().parse_args(["compose", "--no-progress"])
    compose_auto = build_parser().parse_args(["compose"])
    preview = build_parser().parse_args(["preview", "--no-progress"])
    assert synth.progress is True
    assert compose.progress is True
    assert compose_disabled.progress is False
    assert compose_auto.progress is None
    assert preview.progress is False


def test_mastering_cli_options_default_and_inherit_numeric_overrides():
    default_compose = build_parser().parse_args(["compose"])
    custom_compose = build_parser().parse_args(
        [
            "compose",
            "--mastering",
            "broadcast-ebu",
            "--target-lufs",
            "-21",
            "--true-peak-ceiling-dbtp",
            "-2",
        ]
    )
    render = build_parser().parse_args(["render", "text"])
    preview = build_parser().parse_args(["preview"])

    assert default_compose.mastering == "spoken-word"
    assert default_compose.target_lufs is None
    assert default_compose.true_peak_ceiling_dbtp is None
    assert custom_compose.mastering == "broadcast-ebu"
    assert custom_compose.target_lufs == -21
    assert custom_compose.true_peak_ceiling_dbtp == -2
    assert render.mastering == "spoken-word"
    assert preview.mastering == "spoken-word"


def test_compose_progress_stays_on_stderr_and_json_stdout_is_clean(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())

    def compose(_self, project, options=None, *, on_event=None):
        if on_event is not None:
            on_event(
                ReadioEvent(
                    kind="stage.started",
                    operation="projects.compose",
                    stage="composition",
                    message="Preparing composition",
                )
            )
            on_event(
                ReadioEvent(
                    kind="progress",
                    operation="projects.compose",
                    stage="composition",
                    progress_kind="phase",
                    message="Composing audio",
                    details={"clip_items": 1},
                )
            )
            on_event(
                ReadioEvent(
                    kind="progress",
                    operation="projects.compose",
                    stage="composition",
                    progress_kind="phase",
                    message=(
                        "Loudness finalization complete (total 0.010s; "
                        "analysis 0.008s; gain 0.001s; post-gain metrics 0.001s)"
                    ),
                    details={
                        "analysis_seconds": 0.008,
                        "gain_seconds": 0.001,
                        "post_gain_metrics_seconds": 0.001,
                    },
                )
            )
            on_event(
                ReadioEvent(
                    kind="progress",
                    operation="projects.compose",
                    stage="composition",
                    progress_kind="segment.completed",
                    message="Segment complete",
                    completed=1,
                    total=1,
                    segment_id="segment-1",
                )
            )
            on_event(
                ReadioEvent(
                    kind="stage.completed",
                    operation="projects.compose",
                    stage="composition",
                    sample_rate=24000,
                    sample_count=24000,
                    details={"frames": 24000, "items": 1, "sample_rate": 24000},
                )
            )
        return ProjectCompositionResult(
            project=ProjectRef(Path.cwd(), "id", "episode", "audiobook", "ssmd"),
            composition_id="sha256:test",
            frames=24000,
            items=1,
            master_path=Path("master.wav"),
            loudness=LoudnessSummary(
                profile="spoken-word",
                integrated_lufs_before=-20.0,
                integrated_lufs_after=-16.0,
                sample_peak_dbfs_before=-10.0,
                sample_peak_dbfs_after=-6.0,
                true_peak_dbtp_before=-5.0,
                true_peak_dbtp_after=-1.0,
                target_lufs=-16.0,
                true_peak_ceiling_dbtp=-1.0,
                requested_gain_db=4.0,
                applied_gain_db=4.0,
                target_reached=True,
                peak_policy="reduce_gain",
                analysis_seconds=0.1,
                gain_seconds=0.02,
                post_gain_metrics_seconds=0.03,
            ),
        )

    monkeypatch.setattr(ProjectService, "compose", compose)
    args = build_parser().parse_args(["compose", "--progress"])
    assert cli._cmd_compose(args) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "Composition: sha256:test\n"
        "Master: master.wav\n"
        "Mastering profile: spoken-word\n"
        "Integrated loudness: -20.00 → -16.00 LUFS "
        "(target -16.00 LUFS; target reached)\n"
        "True peak: -5.00 → -1.00 dBTP (ceiling -1.00 dBTP)\n"
        "Sample peak: -10.00 → -6.00 dBFS\n"
        "Gain: applied +4.00 dB (requested +4.00 dB)\n"
        "Finalization timing: analysis 0.100s, gain 0.020s, post-gain metrics 0.030s\n"
    )
    assert "Preparing composition…" in captured.err
    assert "Composing" in captured.err
    assert "Loudness finalization complete (total 0.010s" in captured.err
    assert "Composition complete:" in captured.err

    json_args = build_parser().parse_args(["compose", "--json", "--progress"])
    assert cli._cmd_compose(json_args) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["composition_id"] == "sha256:test"
    assert payload["mastering_profile"] == "spoken-word"
    assert payload["loudness"]["integrated_lufs_after"] == -16.0
    assert payload["loudness"]["true_peak_dbtp_after"] == -1.0
    assert "Composing" in captured.err


def test_status_discovers_project_from_nested_directory(tmp_path, monkeypatch, capsys):
    source = tmp_path / "episode.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = __import__("readio.project", fromlist=["init_project"]).init_project(
        source, tmp_path / "episode.readio"
    )
    nested = project.root / "nested" / "deeper"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    args = build_parser().parse_args(["status"])

    assert cli._cmd_status(args) == 0
    output = capsys.readouterr().out
    assert "Readio project:" in output
    assert str(project.root) in output
    assert "Next:\n  readio plan build .\n" in output


def test_short_sentence_cli_accepts_phrase_and_rejects_auto() -> None:
    args = build_parser().parse_args(["speak", "No!", "--short-sentence", "phrase"])
    assert args.short_sentence == "phrase"

    with pytest.raises(SystemExit):
        build_parser().parse_args(["speak", "No!", "--short-sentence", "auto"])


def test_status_cli_renders_domain_command_and_human_issue(monkeypatch, capsys) -> None:
    message = "No exported audio file exists for the current composition."
    project = ProjectRef(Path("/tmp/book.readio"), "project-id", "book", "book", "text")
    result = ProjectStatus(
        project=project,
        stages=(
            StageStatus(stage="source", state="current", reason="current"),
            StageStatus(stage="output", state="stale", reason="output.missing"),
        ),
        issues=(
            Diagnostic(
                code="output.missing",
                severity="warning",
                message=message,
                field="output",
                details={"reason": "output.missing"},
            ),
        ),
        next_actions=(
            NextAction(
                stage="output",
                reason="output.missing",
                command="readio export --format mp3",
            ),
        ),
    )

    class Projects:
        def open(self, _path):
            return project

        def status(self, _project):
            return result

    monkeypatch.setattr(cli, "_api_for", lambda _args: SimpleNamespace(projects=Projects()))
    args = build_parser().parse_args(["status"])

    assert cli._cmd_status(args) == 0
    output = capsys.readouterr().out
    assert message in output
    assert "readio export --format mp3" in output
    assert "output.missing" not in output
    assert "current current" not in output
    assert "readio: 'output'" not in output

    json_args = build_parser().parse_args(["status", "--json"])
    assert cli._cmd_status(json_args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["next_actions"][0] == {
        "stage": "output",
        "reason": "output.missing",
        "command": "readio export --format mp3",
    }


def test_project_settings_cli_inspects_sets_and_clears_supported_sections(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    app = Readio()
    source = tmp_path / "settings.txt"
    source.write_text("Project settings CLI.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "settings.readio")
    monkeypatch.setattr(cli, "_api_for", lambda _args: app)

    def run(args):
        with pytest.raises(SystemExit) as result:
            cli.main(args)
        assert result.value.code == 0
        return json.loads(capsys.readouterr().out)

    initial = run(
        [
            "project",
            "settings",
            "--project",
            str(project.root),
            "--json",
        ]
    )
    assert initial["settings"] == {}

    configured = run(
        [
            "project",
            "settings",
            "set",
            str(project.root),
            "--target-lufs",
            "-18",
            "--export-format",
            "mp3",
            "--export-output",
            "output/saved.mp3",
            "--json",
        ]
    )["settings"]
    assert configured["composition"]["target_lufs"] == -18.0
    assert configured["export"]["format"] == "mp3"
    assert Path(configured["export"]["output"]) == project.root / "output/saved.mp3"

    cleared = run(
        [
            "project",
            "settings",
            "clear",
            str(project.root),
            "--section",
            "composition",
            "--json",
        ]
    )["settings"]
    assert "composition" not in cleared


def test_plan_build_progress_flags_cover_subcommand_and_bare_plan():
    parser = build_parser()
    assert parser.parse_args(["plan", "build", ".", "--progress"]).progress is True
    assert parser.parse_args(["plan", "build", ".", "--no-progress"]).progress is False
    assert parser.parse_args(["plan", "--progress", "build", "."]).progress is True
    assert parser.parse_args(["plan", "--progress"]).progress is True
    assert parser.parse_args(["plan", "--no-progress"]).progress is False


def test_plan_build_help_documents_progress_options(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["plan", "build", "--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--progress" in help_text
    assert "--no-progress" in help_text


def _install_plan_build_stub(monkeypatch):
    from readio.api.types import ProjectPlanResult, ProjectPlanScope

    scope = ProjectPlanScope(scope_id="document", plan_id="plan-id", units=4)
    result = ProjectPlanResult(
        project=ProjectRef(
            root=Path("project"),
            project_id="project-id",
            name="Project",
            kind="document",
            source_format="text",
        ),
        scopes=(scope,),
    )

    def plan(_path, *, on_event=None):
        if on_event is not None:
            on_event(ReadioEvent(kind="stage.started", operation="projects.plan", stage="plan"))
            on_event(
                ReadioEvent(
                    kind="progress",
                    operation="projects.plan",
                    stage="plan",
                    progress_kind="item.started",
                    scope_id="document",
                    completed=0,
                    total=1,
                    details={"scope_index": 1},
                )
            )
            on_event(
                ReadioEvent(
                    kind="progress",
                    operation="projects.plan",
                    stage="plan",
                    progress_kind="phase",
                    message="Loading spaCy model",
                    scope_id="document",
                    details={
                        "phase": "source_analysis",
                        "event_kind": "model.started",
                        "provider": "spacy",
                        "model": "en_core_web_sm",
                    },
                )
            )
            on_event(
                ReadioEvent(
                    kind="progress",
                    operation="projects.plan",
                    stage="plan",
                    progress_kind="item.completed",
                    scope_id="document",
                    completed=1,
                    total=1,
                    details={"scope_index": 1},
                )
            )
            on_event(ReadioEvent(kind="stage.completed", operation="projects.plan", stage="plan"))
        return result

    monkeypatch.setattr(
        cli,
        "_api_for",
        lambda _args: SimpleNamespace(projects=SimpleNamespace(plan=plan)),
    )


def test_plan_build_progress_uses_public_events_and_stderr(monkeypatch, capsys):
    _install_plan_build_stub(monkeypatch)
    args = build_parser().parse_args(["plan", "build", ".", "--progress"])

    assert cli._cmd_plan_build(args) == 0
    captured = capsys.readouterr()
    assert "Semantic plan: plan-id" in captured.out
    assert "Planning…" in captured.err
    assert "Loading spaCy model en_core_web_sm…" in captured.err
    assert "Planning 100%  1/1 scope" in captured.err


def test_plan_build_no_progress_and_json_keep_outputs_clean(monkeypatch, capsys):
    _install_plan_build_stub(monkeypatch)
    args = build_parser().parse_args(["plan", "build", ".", "--no-progress"])

    assert cli._cmd_plan_build(args) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Semantic plan: plan-id" in captured.out

    args = build_parser().parse_args(["plan", "build", ".", "--json", "--progress"])
    assert cli._cmd_plan_build(args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["scopes"][0]["plan_id"] == "plan-id"
    assert "Loading spaCy model en_core_web_sm…" in captured.err


def test_supertonic_voice_cli_lists_semantic_refs_and_count(monkeypatch, capsys):
    voices = tuple(
        VoiceInfo(
            ref=f"supertonic:supertonic-3/{voice_id}",
            id=voice_id,
            gender="unknown",
            language="en",
            locale="en",
            language_label="Multilingual",
            model="supertonic-3",
            source="supertonicsynth",
            default=voice_id == "F1",
            status="ready",
            experimental=False,
            runtime_available=True,
            engine="supertonic",
        )
        for voice_id in ("F1", "M1")
    )
    listing = CatalogListing(voices, CatalogDiscovery(registry_source="fixture"))
    query_calls = []
    catalog = SimpleNamespace(
        engines=lambda: (SimpleNamespace(id="supertonic"),),
        normalize_engine=lambda engine: engine,
        voices_listing=lambda query, **_kwargs: query_calls.append(query) or listing,
    )
    monkeypatch.setattr(cli, "_api_for", lambda _args: SimpleNamespace(catalog=catalog))

    args = build_parser().parse_args(["voices", "list", "--engine", "supertonic", "--lang", "en"])
    assert cli._cmd_voices(args) == 0

    output = capsys.readouterr().out
    assert "Voices: 2" in output
    assert "supertonic:supertonic-3/F1" in output
    assert "supertonic:supertonic-3/M1" in output
    assert query_calls[0].engine == "supertonic"
    assert query_calls[0].language == "en"
    assert cli._normalize_voice_list_filters(
        engine=None,
        model="supertonic",
        available_engines=set(),
        normalize_engine=lambda engine: engine,
    ) == ("supertonic", None)

    assert cli._normalize_voice_list_filters(
        engine=None,
        model="inflectsynth",
        available_engines=set(),
        normalize_engine=lambda value: "inflect" if value == "inflectsynth" else value,
    ) == ("inflect", None)


def test_selftest_e2e_cli_uses_public_api_and_merges_engine_options(monkeypatch, capsys, tmp_path):
    captured = []

    class Verification:
        def run_e2e(self, request):
            captured.append(request)
            return SimpleNamespace(
                overall_status="pass",
                output=tmp_path / "report",
                planning={},
                verification={},
                error=None,
                to_dict=lambda: {"schema": "readio.verification.e2e.v1", "overall_status": "pass"},
            )

    monkeypatch.setattr(cli, "_api_for", lambda _args: SimpleNamespace(verification=Verification()))
    args = build_parser().parse_args(
        [
            "selftest",
            "e2e",
            "--case",
            "pocket-short-tail",
            "--engine",
            "pocket",
            "--model",
            "english_2026-04",
            "--voice",
            "pocket:english_2026-04/alba",
            "--lang",
            "en",
            "--temperature",
            "0.3",
            "--engine-option",
            "frames_after_eos=5",
            "--repetitions",
            "2",
            "--json",
        ]
    )
    assert cli._cmd_selftest_e2e(args) == 0
    request = captured[0]
    assert request.case == "pocket-short-tail"
    assert request.repetitions == 2
    assert request.synthesis.engine_options == {"temperature": 0.3, "frames_after_eos": 5}
    assert request.planning.renderability == "repair"
    assert request.composition.sample_rate == 16_000
    assert json.loads(capsys.readouterr().out)["overall_status"] == "pass"


def test_selftest_timestamps_cli_uses_public_api_and_merged_options(monkeypatch, capsys, tmp_path):
    captured = []

    class Verification:
        def selftest_timestamps(self, request):
            captured.append(request)
            return SimpleNamespace(
                overall_status="pass",
                output=tmp_path / "timestamps",
                summary={"matched_words": 5, "expected_words": 5, "native_comparison_segments": 1},
                error=None,
                to_dict=lambda: {
                    "schema": "readio.verification.timestamp-selftest.v1",
                    "overall_status": "pass",
                },
            )

    monkeypatch.setattr(cli, "_api_for", lambda _args: SimpleNamespace(verification=Verification()))
    args = build_parser().parse_args(
        [
            "selftest",
            "timestamps",
            "--engine",
            "kokoro",
            "--model",
            "v1",
            "--voice",
            "kokoro:v1/af_sarah",
            "--engine-option",
            "speed=1.1",
            "--plan-renderability",
            "strict",
            "--json",
        ]
    )
    assert cli._cmd_selftest_timestamps(args) == 0
    request = captured[0]
    assert request.case == "readio-timestamps-en-v1"
    assert request.synthesis.engine_options == {"speed": 1.1}
    assert request.planning.renderability == "strict"
    assert request.verification.model == "moondream/parakeet-redux"
    assert json.loads(capsys.readouterr().out)["overall_status"] == "pass"


def test_voice_matrix_cli_and_selftest_alias_lower_synthesis_and_discovery_options(
    monkeypatch, capsys, tmp_path
):
    captured = []

    class Verification:
        def generate_voices(self, request):
            captured.append(request)
            return SimpleNamespace(
                overall_status="pass",
                summary={"voice_count": 1, "passed": 1, "review": 0, "failed": 0},
                output=tmp_path / "matrix",
                error=None,
                to_dict=lambda: {
                    "schema": "readio.verification.voice-matrix.v1",
                    "overall_status": "pass",
                },
            )

    monkeypatch.setattr(cli, "_api_for", lambda _args: SimpleNamespace(verification=Verification()))
    args = build_parser().parse_args(
        [
            "voices",
            "generate",
            "--engine",
            "pocket",
            "--model",
            "english_2026-04",
            "--lang",
            "en",
            "--frames-after-eos",
            "5",
            "--engine-option",
            "custom_mode=fast",
            "--offline",
            "--refresh",
            "--preference",
            "huggingface",
            "--include-experimental",
            "--output",
            str(tmp_path / "matrix"),
            "--json",
        ]
    )
    assert cli._cmd_voices_generate(args) == 0
    request = captured[0]
    assert request.synthesis.engine == "pocket"
    assert request.synthesis.model == "english_2026-04"
    assert request.synthesis.voice is None
    assert request.synthesis.engine_options == {"frames_after_eos": 5, "custom_mode": "fast"}
    assert request.discovery.offline is True
    assert request.discovery.refresh is True
    assert request.discovery.preference == "huggingface"
    assert request.include_experimental is True
    assert json.loads(capsys.readouterr().out)["overall_status"] == "pass"

    selftest = build_parser().parse_args(["selftest", "voices", "--engine", "kokoro"])
    assert selftest.func is cli._cmd_voices_generate
