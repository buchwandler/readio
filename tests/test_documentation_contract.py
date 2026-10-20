from __future__ import annotations

from pathlib import Path

from readio.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
CURRENT_DOCS = (
    "README.md",
    "docs/index.md",
    "docs/architecture.md",
    "docs/cli.md",
    "docs/api.md",
    "skill/readio/SKILL.md",
    "skill/readio/references/cli.md",
    "skill/readio/references/input-formats.md",
    "skill/readio/references/ssmd.md",
    "skill/readio/references/troubleshooting.md",
)


def test_documented_readio_commands_exist_in_the_current_cli() -> None:
    parser = build_parser()
    subparsers = next(action for action in parser._actions if action.dest == "command")
    commands = subparsers.choices

    for relative_path in CURRENT_DOCS:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        for line in text.splitlines():
            command_line = line.strip()
            if not command_line.startswith("readio "):
                continue
            command = command_line.split(maxsplit=2)[1]
            if command.startswith("-"):
                continue
            assert command in commands, f"{relative_path} documents unknown command: {command}"


def test_current_docs_explain_native_workspaces_and_no_migration() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    cli = (ROOT / "docs/cli.md").read_text(encoding="utf-8")

    for content in (readme, architecture, cli):
        assert ".ssmdoc" in content
        assert ".ssmdbook" in content
        assert "no migration" in content.lower()

    for relative_path in CURRENT_DOCS:
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "readio audiobook" not in content
        assert "readio config" not in content
