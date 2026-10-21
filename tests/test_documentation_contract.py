from __future__ import annotations

import argparse
import re
from pathlib import Path

from readio.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
CURRENT_DOCS = (
    "README.md",
    "docs/index.md",
    "docs/architecture.md",
    "docs/cli.md",
    "docs/api.md",
    "examples/README.md",
    "skill/readio/SKILL.md",
    "skill/readio/references/cli.md",
    "skill/readio/references/input-formats.md",
    "skill/readio/references/ssmd.md",
    "skill/readio/references/troubleshooting.md",
)


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    action = next(item for item in parser._actions if isinstance(item, argparse._SubParsersAction))
    return action.choices


def test_documented_readio_commands_exist_in_the_current_cli() -> None:
    commands = _subcommands(build_parser())
    bundle_commands = _subcommands(commands["bundle"])

    for relative_path in CURRENT_DOCS:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        for line in text.splitlines():
            command_line = line.strip()
            if not command_line.startswith("readio "):
                continue
            parts = command_line.split()
            if len(parts) < 2:
                continue
            command = parts[1]
            if command.startswith("-"):
                continue
            assert command in commands, f"{relative_path} documents unknown command: {command}"
            if command == "bundle" and len(parts) >= 3 and not parts[2].startswith("-"):
                assert parts[2] in bundle_commands, (
                    f"{relative_path} documents unknown bundle command: {parts[2]}"
                )


def test_workflow_docs_use_one_bundle_contract() -> None:
    selected_docs = (
        "README.md",
        "docs/index.md",
        "docs/architecture.md",
        "docs/cli.md",
        "docs/api.md",
        "examples/README.md",
        "skill/readio/SKILL.md",
        "skill/readio/references/cli.md",
        "skill/readio/references/input-formats.md",
        "skill/readio/references/ssmd.md",
        "skill/readio/references/troubleshooting.md",
    )
    for relative_path in selected_docs:
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert ".ssmd-bundle" in content, relative_path
        assert not re.search(r"\.ssmd(?:doc|book)\b", content), relative_path
        assert not re.search(r"\b(?:compile|render|compose|export)-(?:document|book)\b", content), (
            relative_path
        )
        assert "readio audiobook" not in content
        assert "readio init" not in content
