from __future__ import annotations

import json

import pytest

from readio import cli


def test_generic_project_init_rejects_book_bundle_with_audiobook_guidance(tmp_path, capsys) -> None:
    bundle = tmp_path / "novel.ssmdbook"
    bundle.mkdir()

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--json", "project", "init", str(bundle)])

    assert exit_info.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "input.book_bundle_requires_audiobook"
    assert "use `readio audiobook init`" in payload["error"]
