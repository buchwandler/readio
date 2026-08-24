from readio.text import iter_live_paragraphs


def test_live_paragraphs_emit_on_blank_line_and_eof():
    lines = ["First line.\n", "Still first.\n", "\n", "Second.\n"]
    assert list(iter_live_paragraphs(lines)) == ["First line.\nStill first.", "Second."]


def test_live_paragraphs_skip_multiple_blank_lines():
    assert list(iter_live_paragraphs(["\n", "\n", "Hello\n", "\n", "\n"])) == ["Hello"]
