from pathlib import Path

import pytest

from readio.cli import _validate_live, build_parser


def test_render_parser_has_shared_input_and_output_options():
    args = build_parser().parse_args(
        ["render", "literal", "--file", "episode.ssmd", "--select", "paragraph:2", "-o", "out.wav"]
    )
    assert args.command == "render"
    assert args.text == ["literal"]
    assert args.file == Path("episode.ssmd")
    assert args.select == "paragraph:2"
    assert args.output == Path("out.wav")


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
