from pathlib import Path

import pytest

from readio import cli
from readio.cli import _validate_live, build_parser
from readio.config import PathSettings, ReadioConfig
from readio.document import InputDocument


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
        ["spotify", "text", "--title", "Episode", "--format", "m4a"]
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
    assert parser.parse_args(["spotify", "text", "--title", "Episode"]).progress is None


def test_progress_enablement_respects_tty_and_json():
    class Stream:
        def __init__(self, tty: bool):
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    auto = build_parser().parse_args(["render", "text"])
    assert cli.progress_enabled(auto, Stream(True))
    assert not cli.progress_enabled(auto, Stream(False))

    json_args = build_parser().parse_args(["spotify", "text", "--title", "Episode", "--json"])
    assert not cli.progress_enabled(json_args, Stream(True))
    explicit = build_parser().parse_args(
        ["spotify", "text", "--title", "Episode", "--json", "--progress"]
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
