from pathlib import Path

from readio.markdown import markdown_to_speech

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "markdown"


def test_all_elements_fixture_has_stable_projection():
    source = (FIXTURE_DIR / "all-elements.md").read_text(encoding="utf-8")
    expected = (FIXTURE_DIR / "all-elements.txt").read_text(encoding="utf-8").rstrip("\n")

    assert markdown_to_speech(source) == expected


def test_markdown_markup_is_removed_but_visible_content_remains():
    result = markdown_to_speech(
        "Setext heading\n===\n\nThis is *important*, **critical**, and ~~old~~.\n\n"
        "Read the [guide](https://example.invalid). ![diagram](image.png) and `code`."
    )

    assert "Setext heading." in result
    assert "This is important, critical, and old." in result
    assert "Read the guide." in result
    assert "Image: diagram." in result
    assert "and code." in result
    assert "*" not in result
    assert "**" not in result
    assert "~~" not in result
    assert "https://example.invalid" not in result


def test_lists_tasks_quotes_tables_and_code_are_speech_friendly():
    result = markdown_to_speech(
        "- [x] done\n- [ ] later\n  - nested\n\n"
        "2. first\n3. second\n\n> quoted\n\n"
        "| Name | Role |\n| --- | --- |\n| Sarah | narrator |\n\n"
        "```python\nprint('hello')\n```"
    )

    assert "Checked: done." in result
    assert "Unchecked: later." in result
    assert "Item: nested." in result
    assert "2. first." in result and "3. second." in result
    assert "Quote: quoted." in result
    assert "Table." in result and "Name: Sarah." in result
    assert "Code block." in result and "print('hello')" in result
    assert "| --- |" not in result


def test_html_front_matter_and_footnotes():
    result = markdown_to_speech(
        "---\ntitle: Hidden\n---\n\n<div>Hello <strong>world</strong>.</div>\n"
        "<script>alert('hidden')</script>\n\nA claim.[^1]\n\n[^1]: Supporting detail."
    )

    assert result == "Hello world.\n\nA claim.\n\nFootnote 1. Supporting detail."
    assert "Hidden" not in result
    assert "alert" not in result
    assert "<div>" not in result


def test_markdown_content_cannot_become_ssmd_controls():
    result = markdown_to_speech(
        '`[x]{rate="fast"}`\n\n```text\n<div voice="guest">\n...500ms\n@chapter\n```'
    )

    assert '[x]{rate="fast"}' not in result
    assert '<div voice="guest">' not in result
    assert "...500ms" not in result
    assert "@chapter" not in result
    assert "guest" in result
    assert "500ms" in result
    assert "chapter" in result


def test_metadata_or_empty_alt_image_projects_to_empty():
    assert markdown_to_speech("---\ntitle: metadata only\n---\n") == ""
    assert markdown_to_speech("![](image.png)") == ""


def test_urls_are_visible_but_never_fetched():
    result = markdown_to_speech(
        "[label](https://example.invalid/no-fetch)\n\n<https://example.invalid/visible>"
    )

    assert result == "label\n\nhttps://example.invalid/visible"
