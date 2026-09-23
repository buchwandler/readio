import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import cli
from readio.api.errors import OutputError
from readio.api.events import ReadioEvent
from readio.api.projects import ProjectService
from readio.api.speech import SpeechService
from readio.api.types import ProjectCompositionResult, ProjectRef, RenderResult
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
        conflict = bool(request.output.requested_format and suffix_format != request.output.requested_format)
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
                ] if conflict else [],
            },
        )

    def render(self, request, *, on_event=None, write_manifest=False):
        resolved = self.plan(request)
        output = resolved.output.path
        assert output is not None
        if not resolved.ok:
            raise OutputError(
                "render plan is invalid",
                code="output.format_conflict",
                details={"diagnostics": resolved.to_dict()["diagnostics"]},
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        summary, manifest_path = callback(
            request, resolved, output, on_event, write_manifest
        )
        return RenderResult(
            plan=resolved,
            summary=summary,
            output_path=output,
            manifest_path=manifest_path,
        )

    monkeypatch.setattr(SpeechService, "plan", plan)
    monkeypatch.setattr(SpeechService, "render", render)



def test_single_existing_positional_ssmd_is_normalized_to_file(tmp_path: Path):
    source = tmp_path / "episode.ssmd"
    source.write_text('<div voice="host">Hello.</div>', encoding="utf-8")
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
    assert document.format == "markdown"


def test_existing_positional_txt_is_loaded_as_file(tmp_path: Path):
    source = tmp_path / "notes.txt"
    source.write_text("hello", encoding="utf-8")
    args = build_parser().parse_args(["speak", str(source)])

    cli._normalize_positional_input(args)

    document = cli._read_input(args, ReadioConfig())
    assert document.text == "hello"
    assert document.format == "text"
    assert document.source_path == source


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
    source.write_text('<div voice="host">Hello.</div>', encoding="utf-8")
    link = tmp_path / "linked review.ssmd"
    link.symlink_to(source)
    args = build_parser().parse_args(["speak", str(link)])

    cli._normalize_positional_input(args)

    assert args.file == link
    assert cli._read_input(args, ReadioConfig()).source_path == link


def test_positional_ssmd_is_passed_to_the_public_speech_service(monkeypatch, tmp_path: Path):
    source = tmp_path / "episode.ssmd"
    body = '<div voice="host">File body.</div>'
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
    assert document.text == body


def test_render_resolves_output_with_normalized_positional_path(monkeypatch, tmp_path: Path):
    source = tmp_path / "episode.ssmd"
    source.write_text('<div voice="host">Hello.</div>', encoding="utf-8")
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
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
    assert "explicit text disables" in help_text
    assert "positional file detection" in help_text


@pytest.mark.parametrize("command", ["speak", "render"])
def test_pause_mode_cli_is_unset_when_omitted(command):
    args = build_parser().parse_args([command, "hello"])
    assert args.pause_mode is None


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
        build_parser().parse_args(["render", "text", "--format", "flac"])


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


def test_piper_live_mode_is_rejected(monkeypatch):
    from readio.config import ReadioConfig

    cfg = ReadioConfig()
    monkeypatch.setattr(cli, "_resolved_config", lambda args: cfg)
    for command in ("speak", "render"):
        args = build_parser().parse_args([command, "--live", "--engine", "piper"])
        with pytest.raises(ValueError, match="Live streaming is not yet supported"):
            if command == "speak":
                cli._cmd_speak(args)
            else:
                cli._cmd_render(args)


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


def test_speak_uses_the_public_speech_service(monkeypatch):
    captured = []
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())
    monkeypatch.setattr(
        SpeechService,
        "speak",
        lambda _self, request, *, on_event=None: captured.append(request),
    )

    args = build_parser().parse_args(["speak", "hello"] )
    assert cli._cmd_speak(args) == 0
    assert captured[0].operation == "speak"
    assert captured[0].input.document.text == "hello"


def test_render_uses_selected_format_and_output_suffix(monkeypatch, tmp_path: Path):
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
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
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
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


def test_no_progress_suppresses_render_status(monkeypatch, capsys, tmp_path: Path):
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
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
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
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
                    message="compose_started",
                    completed=0,
                    total=1,
                    sample_rate=24000,
                    details={"clip_items": 1},
                )
            )
            on_event(
                ReadioEvent(
                    kind="progress",
                    operation="projects.compose",
                    stage="composition",
                    message="compose_completed",
                    completed=1,
                    total=1,
                    sample_rate=24000,
                    sample_count=24000,
                )
            )
        return ProjectCompositionResult(
            project=ProjectRef(Path.cwd(), "id", "episode", "audiobook", "ssmd"),
            composition_id="sha256:test",
            frames=24000,
            items=1,
            master_path=Path("master.wav"),
        )

    monkeypatch.setattr(ProjectService, "compose", compose)
    args = build_parser().parse_args(["compose", "--progress"])
    assert cli._cmd_compose(args) == 0
    captured = capsys.readouterr()
    assert captured.out == "Composition: sha256:test\nMaster: master.wav\n"
    assert "Preparing composition…" in captured.err
    assert "Composing" in captured.err
    assert "Composition complete:" in captured.err

    json_args = build_parser().parse_args(["compose", "--json", "--progress"])
    assert cli._cmd_compose(json_args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "ok": True,
        "composition_id": "sha256:test",
        "master": "master.wav",
        "frames": 24000,
        "items": 1,
    }
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
    assert "readio plan" in output
