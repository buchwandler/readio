from pathlib import Path

import pytest

from readio.ingest import new_ingest
from readio.templates import (
    add_template,
    list_templates,
    packaged_template,
    reset_template,
    seed_templates,
    show_template,
)


def test_seed_templates_copies_packaged_defaults(tmp_path: Path):
    seed_templates(tmp_path)
    assert list_templates(tmp_path) == ["briefing", "dialogue", "podcast"]
    assert show_template(tmp_path, "podcast") == packaged_template("podcast")


def test_seed_templates_does_not_overwrite_user_changes(tmp_path: Path):
    seed_templates(tmp_path)
    path = tmp_path / "podcast.ssmd"
    path.write_text("custom", encoding="utf-8")
    seed_templates(tmp_path)
    assert path.read_text(encoding="utf-8") == "custom"


def test_reset_template_restores_packaged_default(tmp_path: Path):
    seed_templates(tmp_path)
    path = tmp_path / "podcast.ssmd"
    path.write_text("custom", encoding="utf-8")
    reset_template(tmp_path, "podcast")
    assert path.read_text(encoding="utf-8") == packaged_template("podcast")


def test_add_template_rejects_existing_without_force_and_force_replaces(tmp_path: Path):
    source = tmp_path / "source.ssmd"
    source.write_text("one", encoding="utf-8")
    add_template(tmp_path / "templates", "custom", source)
    with pytest.raises(ValueError, match="already exists"):
        add_template(tmp_path / "templates", "custom", source)
    source.write_text("two", encoding="utf-8")
    add_template(tmp_path / "templates", "custom", source, force=True)
    assert show_template(tmp_path / "templates", "custom") == "two"


def test_template_name_rejects_traversal(tmp_path: Path):
    with pytest.raises(ValueError, match="unsafe"):
        add_template(tmp_path, "../bad", content="x")


def test_template_use_copies_to_ingest(tmp_path: Path):
    templates = tmp_path / "templates"
    ingest = tmp_path / "ingest"
    seed_templates(templates)
    target = new_ingest(ingest, template_directory=templates, template="podcast")
    assert target.suffix == ".ssmd"
    assert target.read_text(encoding="utf-8") == packaged_template("podcast")
