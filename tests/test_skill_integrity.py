import re
from pathlib import Path

SKILL_DIR = Path(__file__).parents[1] / "skill" / "readio"


def test_skill_references_exist_and_stay_inside_skill_directory():
    root = SKILL_DIR / "SKILL.md"
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", root.read_text(encoding="utf-8"))
    assert links
    for link in links:
        assert not link.startswith(("http:", "https:", "#"))
        target = (root.parent / link).resolve()
        assert target.is_relative_to(SKILL_DIR.resolve())
        assert target.is_file()


def test_skill_documents_current_spotify_commands():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    spotify = (SKILL_DIR / "references" / "spotify.md").read_text(encoding="utf-8")
    for command in ("publish", "upload", "shows", "status", "doctor"):
        assert f"spotify {command}" in spotify
    assert "references/spotify.md" in text
