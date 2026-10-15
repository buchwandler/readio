from __future__ import annotations

import json

import pytest
from project_support import Adapter, request

from readio.config import ReaderSettings, ReadioConfig
from readio.engines.registry import _registry
from readio.planning.flow import iter_flow_segments
from readio.project import init_project
from readio.stages import synthesis as synthesis_stage
from readio.stages.planning import load_scope_plan, plan_project


def _planned_project(tmp_path, monkeypatch, adapter: Adapter, text: str = "First. Second."):
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice", spacy="off"))
    source = tmp_path / "document.txt"
    source.write_text(text, encoding="utf-8")
    project = init_project(source, tmp_path / "document.readio")
    plan_project(project, cfg)
    scope = project.load_plan_index().scopes[0]
    plan = load_scope_plan(project, scope)
    return cfg, project, scope, plan


def test_project_synthesis_traverses_schema_v7_locators(tmp_path, monkeypatch) -> None:
    adapter = Adapter()
    cfg, project, scope, plan = _planned_project(tmp_path, monkeypatch, adapter)
    refs = tuple(iter_flow_segments(plan))
    events = []

    result = synthesis_stage.synthesize_project(
        project,
        cfg,
        request=request(project),
        on_event=events.append,
    )

    assert len(result["artifacts"]) == len(refs)
    assert adapter.open_calls == 1
    trace = json.loads(project.paths["synthesis_trace"].read_text(encoding="utf-8"))
    assert [item["source_locator"] for item in trace["segments"]] == [ref.id for ref in refs]
    assert all(item.source_plan_id == plan.plan_id for item in result["artifacts"])
    assert any(
        event.kind == "segment_finished"
        and event.details.get("voicerender_status") == "segment_rendered"
        for event in events
    )
    assert scope.id == "document"


def test_project_synthesis_rejects_adapter_resolution_before_opening_session(
    tmp_path, monkeypatch
) -> None:
    class RejectingAdapter(Adapter):
        def resolve(self, _request):
            raise ValueError("fixture target resolution failed")

    adapter = RejectingAdapter()
    cfg, project, _scope, _plan = _planned_project(tmp_path, monkeypatch, adapter)

    with pytest.raises(ValueError, match="fixture target resolution failed"):
        synthesis_stage.synthesize_project(project, cfg, request=request(project))

    assert adapter.open_calls == 0
    assert not list((project.state_root / "synthesis").rglob("*.wav"))
