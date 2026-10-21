from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bundle_stage_routing_never_branches_on_manifest_kind() -> None:
    paths = (
        ROOT / "readio/pipeline.py",
        *sorted((ROOT / "readio/integrations").glob("*.py")),
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "workspace.kind" not in source
    stages = (
        "compile_bundle",
        "render_bundle_plan",
        "render_bundle",
        "compose_bundle",
        "export_bundle",
    )
    for stage in stages:
        assert stage in source
