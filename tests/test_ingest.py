from pathlib import Path

import pytest

from readio.ingest import list_ingest, new_ingest
from readio.templates import seed_templates


def test_ingest_new_default_txt(tmp_path: Path):
    target = new_ingest(tmp_path)
    assert target.suffix == ".txt"
    assert target.read_text(encoding="utf-8") == ""


def test_ingest_new_template_defaults_to_ssmd(tmp_path: Path):
    templates = tmp_path / "templates"
    seed_templates(templates)
    target = new_ingest(tmp_path / "ingest", template_directory=templates, template="briefing")
    assert target.suffix == ".ssmd"


def test_ingest_explicit_name_and_listing(tmp_path: Path):
    target = new_ingest(tmp_path, name="notes.md")
    assert target.name == "notes.md"
    assert [path.name for path in list_ingest(tmp_path)] == ["notes.md"]
    with pytest.raises(ValueError, match="already exists"):
        new_ingest(tmp_path, name="notes.md")


def test_ingest_rejects_traversal(tmp_path: Path):
    with pytest.raises(ValueError, match="unsafe"):
        new_ingest(tmp_path, name="../notes.txt")
