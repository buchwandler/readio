import json
from pathlib import Path

import pytest

from readio import cli
from readio.audio import RenderSummary
from readio.cli import _validate_live, build_parser
from readio.config import PathSettings, ReadioConfig
from readio.document import InputDocument


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


def test_positional_ssmd_reaches_preflight(monkeypatch, tmp_path: Path):
    source = tmp_path / "episode.ssmd"
    body = '<div voice="host">File body.</div>'
    source.write_text(body, encoding="utf-8")
    args = build_parser().parse_args(["speak", str(source)])
    calls = []

    def analyze(text, cfg, *, source_path, additional_bindings, synthesis):
        calls.append(("analyze", text, source_path))
        return type("Analysis", (), {"unresolved_voice_references": ()})()

    def preflight(text, cfg, *, source_path, additional_bindings, synthesis):
        calls.append(("preflight", text, source_path))

    monkeypatch.setattr(cli, "analyze_ssmd", analyze)
    monkeypatch.setattr(cli, "preflight_ssmd", preflight)
    cli._normalize_positional_input(args)
    document, _ = cli._prepared_input(args, ReadioConfig())

    assert document.format == "ssmd"
    assert document.source_path == source
    assert document.text == body
    assert [call[0] for call in calls] == ["analyze", "preflight"]
    assert all(call[1:] == (body, source) for call in calls)


def test_render_resolves_output_with_normalized_positional_path(monkeypatch, tmp_path: Path):
    source = tmp_path / "episode.ssmd"
    source.write_text('<div voice="host">Hello.</div>', encoding="utf-8")
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
    captured = []
    monkeypatch.setattr(cli, "_resolved_config", lambda args: cfg)
    monkeypatch.setattr(cli, "resolve_synthesis", lambda cfg, args: None)
    monkeypatch.setattr(
        cli,
        "_prepared_input",
        lambda args, cfg: (InputDocument("body", source, "ssmd"), {}),
    )
    monkeypatch.setattr(
        cli,
        "resolve_render_output",
        lambda cfg, *, explicit, input_path, audio_format: (
            captured.append(input_path) or tmp_path / "episode.wav"
        ),
    )

    def render(args, path, *, audio_format, **kwargs):
        path.write_bytes(b"wav")
        return RenderSummary(sample_rate=24000, sample_count=24000, channels=1)

    monkeypatch.setattr(cli, "_render_audio", render)
    args = build_parser().parse_args(["render", str(source), "--no-progress"])

    assert cli._cmd_render(args) == 0
    assert captured == [source]


def test_missing_positional_path_fails_before_synthesis(monkeypatch, tmp_path: Path):
    args = build_parser().parse_args(["render", str(tmp_path / "missing.ssmd")])
    monkeypatch.setattr(cli, "resolve_synthesis", lambda *args: pytest.fail("synthesis resolved"))

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


def test_render_uses_selected_format_and_output_suffix(monkeypatch, tmp_path: Path):
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
    calls = []
    monkeypatch.setattr(cli, "_resolved_config", lambda args: cfg)
    monkeypatch.setattr(
        cli,
        "_prepared_input",
        lambda args, cfg: (InputDocument("text", None, "text"), {}),
    )

    def render(args, path, *, audio_format, **kwargs):
        calls.append((path, audio_format))
        path.write_bytes(b"mp3")

    monkeypatch.setattr(cli, "_render_audio", render)
    output = tmp_path / "episode.mp3"
    args = build_parser().parse_args(["render", "text", "-o", str(output)])
    assert cli._cmd_render(args) == 0
    assert output.read_bytes() == b"mp3"
    assert calls[0][1] == "mp3"
    assert calls[0][0].suffix == ".mp3"


def test_render_rejects_format_conflict_before_input_preparation(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cli, "_prepared_input", lambda *args: pytest.fail("input was prepared"))
    args = build_parser().parse_args(
        ["render", "text", "--format", "mp3", "-o", str(tmp_path / "episode.ogg")]
    )
    with pytest.raises(ValueError, match="conflicts with output extension"):
        cli._cmd_render(args)


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
    monkeypatch.setattr(cli, "_resolved_config", lambda args: cfg)
    monkeypatch.setattr(
        cli,
        "_prepared_input",
        lambda args, cfg: (InputDocument("text", None, "text"), {}),
    )

    def render(args, path, *, audio_format, **kwargs):
        path.write_bytes(b"wav")
        if "on_phase" in kwargs:
            kwargs["on_phase"]("Finalizing WAV")
        return cli.RenderSummary(sample_rate=24000, sample_count=24000, channels=1)

    monkeypatch.setattr(cli, "_render_audio", render)
    output = tmp_path / "episode.wav"
    args = build_parser().parse_args(["render", "text", "--progress", "-o", str(output)])

    assert cli._cmd_render(args) == 0
    captured = capsys.readouterr()
    assert captured.out == f"{output}\n"
    assert "Preparing" in captured.err
    assert "Finalizing" in captured.err
    assert "Rendered 0 units" in captured.err


def test_no_progress_suppresses_render_status(monkeypatch, capsys, tmp_path: Path):
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
    monkeypatch.setattr(cli, "_resolved_config", lambda args: cfg)
    monkeypatch.setattr(
        cli,
        "_prepared_input",
        lambda args, cfg: (InputDocument("text", None, "text"), {}),
    )
    monkeypatch.setattr(
        cli,
        "_render_audio",
        lambda args, path, *, audio_format: path.write_bytes(b"wav"),
    )
    output = tmp_path / "episode.wav"
    args = build_parser().parse_args(["render", "text", "--no-progress", "-o", str(output)])

    assert cli._cmd_render(args) == 0
    assert capsys.readouterr().err == ""


def test_render_json_reports_stable_envelope(monkeypatch, capsys, tmp_path: Path):
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
    monkeypatch.setattr(cli, "_resolved_config", lambda args: cfg)
    monkeypatch.setattr(
        cli,
        "_prepared_input",
        lambda args, cfg: (InputDocument("text", None, "text"), {}),
    )

    def render(args, path, *, audio_format, **kwargs):
        path.write_bytes(b"wav")
        return RenderSummary(sample_rate=24000, sample_count=24000, channels=1)

    monkeypatch.setattr(cli, "_render_audio", render)
    output = tmp_path / "episode.wav"
    args = build_parser().parse_args(["render", "text", "--json", "-o", str(output)])

    assert cli._cmd_render(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["duration_ms"] == 1000
    assert result["path"] == str(output)
