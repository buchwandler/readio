from __future__ import annotations

import json
from contextlib import contextmanager

import numpy as np
import pytest
from project_support import Adapter, request

from readio.config import ReaderSettings, ReadioConfig
from readio.engines.base import RenderedSpeech, RequestMeasure
from readio.engines.registry import _registry
from readio.project import init_project, read_json
from readio.stages.planning import load_scope_plan, plan_project
from readio.stages.synthesis import synthesize_project


class _CapacitySession:
    def __init__(self, adapter: _CapacityAdapter, maximum: int) -> None:
        self.adapter = adapter
        self.maximum = maximum

    def measure(self, request):
        amount = len(request.text)
        return RequestMeasure(
            fits=amount <= self.maximum,
            amount=amount,
            maximum=self.maximum,
            unit="model_tokens",
            source="test.project_capacity",
        )

    def synthesize(self, request):
        self.adapter.requests.append(request)
        assert len(request.text) <= self.maximum
        return RenderedSpeech(
            id=request.id,
            audio=np.full(max(1, len(request.text)), 0.1, dtype=np.float32),
            sample_rate=24_000,
        )


class _CapacityAdapter(Adapter):
    def __init__(self, maximum: int) -> None:
        super().__init__()
        self.session = _CapacitySession(self, maximum)
        self.requests = []

    def open(self, selection):
        self.open_calls += 1

        @contextmanager
        def session_context():
            yield self.session

        return session_context()


@pytest.mark.parametrize(
    ("text", "maximum", "split"),
    [("alpha beta gamma", 6, True), ("Hello.", 100, False)],
)
def test_project_persists_exact_atomic_manifest_and_reuses_cached_segment(
    tmp_path, monkeypatch, text: str, maximum: int, split: bool
) -> None:
    adapter = _CapacityAdapter(maximum)
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    config = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice", spacy="off"))
    source = tmp_path / "book.txt"
    source.write_text(text, encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, config)

    first = synthesize_project(project, config, request=request(project))
    trace = read_json(project.paths["synthesis_trace"])
    scope = project.load_plan_index().scopes[0]
    plan = load_scope_plan(project, scope)
    segments = {str(segment.id): segment for segment in plan.segments}
    rows = trace["segments"]
    sidecars = [read_json(project.root / row["sidecar_path"]) for row in rows]

    assert first["rendered"] == len(rows)
    assert all(sidecar["schema_version"] == 3 for sidecar in sidecars)
    assert all(sidecar["lowering_sha256"] for sidecar in sidecars)
    assert all(sidecar["lowering"]["schema"] == "readio.atomic-lowering.v1" for sidecar in sidecars)
    for row, sidecar in zip(rows, sidecars, strict=True):
        manifest = sidecar["lowering"]
        requests = manifest["requests"]
        expected_text = segments[row["segment_id"]].text
        assert "".join(child["text"] for child in requests) == expected_text
        assert all(child["request"]["text"] == child["text"] for child in requests)
        assert all(child["measure"]["source"] == "test.project_capacity" for child in requests)

    assert any(len(sidecar["lowering"]["requests"]) > 1 for sidecar in sidecars) is split
    rendered_count = len(adapter.requests)
    open_count = adapter.open_calls

    cached = synthesize_project(project, config, request=request(project))

    assert cached["rendered"] == 0
    assert cached["reused"] == len(rows)
    assert len(adapter.requests) == rendered_count
    assert adapter.open_calls == open_count

    sidecar_path = project.root / rows[0]["sidecar_path"]
    invalid_sidecar = read_json(sidecar_path)
    invalid_sidecar["schema_version"] = 2
    sidecar_path.write_text(json.dumps(invalid_sidecar), encoding="utf-8")
    calls_before_repair = len(adapter.requests)

    repaired = synthesize_project(project, config, request=request(project))

    assert repaired["rendered"] >= 1
    assert len(adapter.requests) > calls_before_repair
    assert read_json(sidecar_path)["schema_version"] == 3
