from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from readio.config import ReaderSettings, ReadioConfig
from readio.engines.base import EngineCapabilities, EngineSelection
from readio.engines.registry import _registry
from readio.plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest
from readio.project import hash_file, init_project, update_project_manifest
from readio.project_model import DocumentIndex, DocumentScope
from readio.project_settings import with_project_voice_binding, with_project_voice_provider
from readio.stages.pipeline import project_status
from readio.stages.planning import plan_project
from readio.stages.synthesis import synthesis_profile_id, synthesize_project


def test_project_voice_binding_change_stales_synthesis_without_replanning_or_cache_deletion(
    tmp_path, monkeypatch
):
    adapter = _Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "episode.ssmd"
    source.write_text('<div voice="narrator">Hello.</div>', encoding="utf-8")
    project = init_project(source, tmp_path / "episode.readio")
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="narrator", voice="af_heart"
        ),
    )
    planned = plan_project(project, cfg)
    plan_id = planned.scopes[0].compiled.plan_id
    synthesize_project(project, cfg, request=_request(project))

    profile = json.loads(project.paths["synthesis_profile"].read_text(encoding="utf-8"))
    assert profile["project_voice_bindings"]["provider"] == "kokoro"
    assert profile["project_voice_bindings"]["bindings"] == {"narrator": "af_heart"}
    assert profile["project_voice_bindings"]["sha256"].startswith("sha256:")
    assert profile["profile_id"] == synthesis_profile_id(
        {"schema": profile["schema"], "canonical": profile["canonical"]}
    )
    cache_files = set((project.root / "synthesis" / "cache").glob("*.wav"))
    assert cache_files
    before = {row["stage"]: row for row in project_status(project)["stages"]}
    assert before["synthesis"]["state"] == "current"

    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="narrator", voice="af_bella"
        ),
    )
    replanned = plan_project(project, cfg)
    assert replanned.scopes[0].compiled.plan_id == plan_id

    status = project_status(project)
    stages = {row["stage"]: row for row in status["stages"]}
    assert stages["plan"]["state"] == "current"
    assert stages["synthesis"]["reason"] == ("synthesis.stale.project_voice_bindings_changed")
    assert stages["composition"]["blocked_by"] == "synthesis"
    assert stages["output"]["blocked_by"] == "composition"
    assert status["next_actions"][0]["command"] == "readio synth"
    assert set((project.root / "synthesis" / "cache").glob("*.wav")) == cache_files


class _Result:
    def __init__(self, index: int):
        self.descriptor = SimpleNamespace(index=index)
        self.audio = np.full(160, 0.1, dtype=np.float32)
        self.sample_rate = 24000
        self.markers = []

    def release_audio(self):
        dtype = self.audio.dtype
        self.audio = np.empty(0, dtype=dtype)


class _Prepared:
    def __init__(self, plan):
        self.plan = plan
        self.render_calls = 0
        self.render_indices = ()
        self.generated_indices = []
        self.results = []
        self.output_indices = None
        self._render_started = False
        self._active = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self._active is not None:
            self._active.release_audio()
            self._active = None

    def render(self, indices=None):
        if self._render_started:
            raise RuntimeError("prepared renderer supports one render pass only")
        self._render_started = True
        self.render_calls += 1
        ordered = tuple(indices) if indices is not None else tuple(range(len(self.plan.units)))
        self.render_indices = ordered
        emitted = self.output_indices if self.output_indices is not None else ordered

        def iterate():
            previous = None
            try:
                for index in emitted:
                    if previous is not None:
                        previous.release_audio()
                    previous = _Result(index)
                    self.results.append(previous)
                    self._active = previous
                    self.generated_indices.append(index)
                    yield previous
            finally:
                if previous is not None:
                    previous.release_audio()
                self._active = None

        return iterate()


class _Session:
    def __init__(self, adapter):
        self.adapter = adapter

    def prepare_plan(self, plan, *, options):
        prepared = self.adapter.prepared_factory(plan)
        self.adapter.prepared.append(prepared)
        return prepared


class _Adapter:
    id = "fake"

    def __init__(self, prepared_factory=_Prepared):
        self.open_calls = 0
        self.prepared_factory = prepared_factory
        self.prepared = []

    def version(self):
        return "fake-1"

    def capabilities(self):
        return EngineCapabilities(id=self.id)

    def resolve(self, request):
        return EngineSelection(
            self.id,
            "fake-target",
            request.language or "en-us",
            voice=request.voice,
            options=dict(request.options),
        ), ()

    def planner_config(self, selection, planning):
        return None

    def open(self, selection):
        self.open_calls += 1

        @contextmanager
        def session():
            yield _Session(self)

        return session()


class _TargetResult(_Result):
    def __init__(self, index: int, segment_id: str):
        super().__init__(index)
        self.segment_id = segment_id


class _TargetPrepared:
    def __init__(self, adapter, selection):
        self.adapter = adapter
        self.selection = selection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def render(self, *, segment_ids):
        target = self.selection.target_id
        self.adapter.rendered_by_target.setdefault(target, []).extend(segment_ids)
        for index, segment_id in enumerate(segment_ids):
            yield _TargetResult(index, segment_id)


class _TargetSession:
    def __init__(self, adapter, selection):
        self.adapter = adapter
        self.selection = selection

    def prepare_segments(self, plan, *, options):
        return _TargetPrepared(self.adapter, self.selection)


class _TargetAdapter:
    id = "fake-target"

    def __init__(self, valid_targets=("voice-g", "voice-h", "voice-n")):
        self.valid_targets = set(valid_targets)
        self.open_calls = []
        self.opened_selections = []
        self.rendered_by_target = {}

    def version(self):
        return "fake-target-1"

    def capabilities(self):
        return EngineCapabilities(
            id=self.id,
            ssmd_provider="fake-target-provider",
            ssmd_voice_binding_mode="target",
        )

    def resolve(self, request):
        target = request.target_id or request.voice
        return (
            EngineSelection(
                engine=self.id,
                target_id=target,
                language=request.language or "en-us",
                voice=request.voice or target,
                speaker=request.speaker,
                options=dict(request.options),
                offline=request.offline,
                refresh=request.refresh,
            ),
            (),
        )

    def validate_selection(self, selection):
        if selection.target_id not in self.valid_targets:
            return (
                SimpleNamespace(
                    severity="error",
                    message=f"unknown target {selection.target_id!r}",
                ),
            )
        return ()

    def canonical_synthesis_identity(self, selection):
        return {
            "engine": self.id,
            "engine_version": self.version(),
            "target_id": selection.target_id,
            "voice": selection.voice,
            "options": dict(selection.options),
        }

    def planner_config(self, selection, planning):
        return None

    def open(self, selection):
        self.open_calls.append(selection.target_id)
        self.opened_selections.append(selection)

        @contextmanager
        def session():
            yield _TargetSession(self, selection)

        return session()


class _PiperTargetAdapter(_TargetAdapter):
    id = "piper"

    def capabilities(self):
        return EngineCapabilities(
            id=self.id,
            ssmd_provider="piper",
            ssmd_voice_binding_mode="target",
        )


def _target_project(tmp_path, monkeypatch, bindings):
    adapter = _TargetAdapter()
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine=adapter.id, voice="voice-n", spacy="off"))
    source = tmp_path / "target-project.ssmd"
    source.write_text(
        '<div voice="narrator">N1.</div>\n'
        '<div voice="guest">G1.</div>\n'
        '<div voice="narrator">N2.</div>\n'
        '<div voice="host">H1.</div>',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "target-project.readio")

    def update(manifest):
        manifest = with_project_voice_provider(manifest, "fake-target-provider")
        for role, voice in bindings.items():
            manifest = with_project_voice_binding(
                manifest,
                provider="fake-target-provider",
                role=role,
                voice=voice,
            )
        return manifest

    project = update_project_manifest(project, update)
    plan_project(project, cfg)
    return project, cfg, adapter


def test_target_routing_groups_segments_once_per_voice_target(tmp_path, monkeypatch):
    bindings = {
        "narrator": "voice-n",
        "guest": "voice-g",
        "host": "voice-h",
    }
    project, cfg, adapter = _target_project(tmp_path, monkeypatch, bindings)
    plan_scope = project.load_plan_index().scopes[0]
    original_plan_id = plan_scope.plan_id
    request = PlanRequest(
        "render",
        InputRequest(project.load_document_scope(project.document_scopes()[0])),
        SynthesisRequest(engine=adapter.id),
    )
    events = []

    result = synthesize_project(project, cfg, request=request, on_event=events.append)

    from readio.stages.planning import load_scope_plan

    plan = load_scope_plan(project, plan_scope)
    expected = {}
    for segment in plan.segments:
        reference = segment.directives.voice.reference
        expected.setdefault(bindings[reference], []).append(str(segment.id))

    assert adapter.open_calls == ["voice-g", "voice-h", "voice-n"]
    assert adapter.rendered_by_target == expected
    assert result["rendered"] == 4
    assert result["activated"] is True
    assert project.load_plan_index().scopes[0].plan_id == original_plan_id
    assert [
        event.details["target_id"] for event in events if event.kind == "engine_open_started"
    ] == ["voice-g", "voice-h", "voice-n"]
    assert all(artifact.path.is_file() for artifact in result["artifacts"])
    profile = json.loads(project.paths["synthesis_profile"].read_text(encoding="utf-8"))
    assert profile["schema_version"] == 3
    assert profile["schema"] == "readio.synthesis-profile.v3"
    assert profile["canonical"]["routing_mode"] == "target"
    assert set(profile["canonical"]["targets"]) == {"voice-g", "voice-h", "voice-n"}
    assert profile["canonical"]["bindings_by_scope"] == {"document": bindings}
    assert profile["project_voice_bindings"]["provider"] == "fake-target-provider"
    assert (
        profile["canonical"]["project_voice_bindings_sha256"]
        == profile["project_voice_bindings"]["sha256"]
    )
    stages = {item["stage"]: item for item in project_status(project)["stages"]}
    assert stages["synthesis"]["state"] == "current"

    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest,
            provider="fake-target-provider",
            role="guest",
            voice="voice-h",
        ),
    )
    stages = {item["stage"]: item for item in project_status(project)["stages"]}
    assert stages["synthesis"]["reason"] == ("synthesis.stale.project_voice_bindings_changed")


def test_target_routing_validates_all_targets_before_opening_any_session(tmp_path, monkeypatch):
    project, cfg, adapter = _target_project(
        tmp_path,
        monkeypatch,
        {
            "narrator": "voice-n",
            "guest": "voice-invalid",
            "host": "voice-h",
        },
    )
    request = PlanRequest(
        "render",
        InputRequest(project.load_document_scope(project.document_scopes()[0])),
        SynthesisRequest(engine=adapter.id),
    )

    with pytest.raises(ValueError, match="unknown target 'voice-invalid'"):
        synthesize_project(project, cfg, request=request)

    assert adapter.open_calls == []


def test_target_routing_fails_unresolved_roles_before_opening_any_session(tmp_path, monkeypatch):
    project, cfg, adapter = _target_project(
        tmp_path, monkeypatch, {"narrator": "voice-n", "host": "voice-h"}
    )
    request = PlanRequest(
        "render",
        InputRequest(project.load_document_scope(project.document_scopes()[0])),
        SynthesisRequest(engine=adapter.id),
    )

    with pytest.raises(ValueError, match="cannot resolve voice reference 'guest'"):
        synthesize_project(project, cfg, request=request)

    assert adapter.open_calls == []


def test_project_provider_selects_piper_instead_of_global_engine_or_voice(tmp_path, monkeypatch):
    target_voice = "en_US-amy-medium"
    adapter = _PiperTargetAdapter(valid_targets=(target_voice,))
    monkeypatch.setitem(_registry._adapters, "piper", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="pykokoro", voice="af_sarah", spacy="off"))
    source = tmp_path / "piper-default.ssmd"
    source.write_text('<div voice="narrator">Hello.</div>', encoding="utf-8")
    project = init_project(source, tmp_path / "piper-default.readio")
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            with_project_voice_provider(manifest, "piper"),
            provider="piper",
            role="narrator",
            voice=target_voice,
        ),
    )
    plan_project(project, cfg)
    request = PlanRequest(
        "render",
        InputRequest(project.load_document_scope(project.document_scopes()[0])),
        SynthesisRequest(),
    )

    result = synthesize_project(project, cfg, request=request)

    assert adapter.open_calls == [target_voice]
    assert len(adapter.opened_selections) == 1
    selection = adapter.opened_selections[0]
    assert selection.engine == "piper"
    assert selection.voice == target_voice
    assert selection.voice != "af_sarah"
    assert result["rendered"] == 1


def _request(project):
    return PlanRequest(
        "render",
        InputRequest(project.document()),
        SynthesisRequest(engine="fake", voice="fake-voice"),
        OutputRequest(mode="file", requested_format="wav", force=True),
    )


def _prepared_factory(*indices):
    def factory(plan):
        prepared = _Prepared(plan)
        prepared.output_indices = tuple(indices)
        return prepared

    return factory


def test_complete_cache_hit_does_not_open_engine_and_edit_rebuilds_one_unit(tmp_path, monkeypatch):
    adapter = _Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.\n\nGamma.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)
    first = synthesize_project(project, cfg, request=_request(project))
    assert first["rendered"] == len(first["selection"].unit_indices)
    assert adapter.open_calls == 1
    assert len(adapter.prepared) == 1
    assert adapter.prepared[0].render_calls == 1
    assert adapter.prepared[0].render_indices == tuple(first["selection"].unit_indices)
    cache_paths = sorted((project.root / "synthesis" / "cache").glob("*.wav"))
    assert len(cache_paths) == 3
    assert all(sf.info(path).frames > 0 for path in cache_paths)

    second = synthesize_project(project, cfg, request=_request(project))
    assert second["rendered"] == 0
    assert second["reused"] == first["rendered"]
    assert adapter.open_calls == 1
    assert len(adapter.prepared) == 1

    project_source = project.root / "source" / "book.txt"
    project_source.write_text("Alpha.\n\nChanged.\n\nGamma.", encoding="utf-8")
    plan_project(project, cfg)
    third = synthesize_project(project, cfg, request=_request(project))
    assert third["rendered"] == 1
    assert third["reused"] == 2
    assert adapter.open_calls == 2
    assert len(adapter.prepared) == 2
    assert adapter.prepared[1].render_calls == 1
    assert adapter.prepared[1].render_indices == (1,)


def test_project_synthesis_emits_lifecycle_events_and_skips_engine_on_cache_hit(
    tmp_path, monkeypatch
):
    adapter = _Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)
    events = []

    def on_event(event):
        if event.kind == "unit_started":
            assert event.unit_index not in adapter.prepared[-1].generated_indices
        events.append(event)

    synthesize_project(project, cfg, request=_request(project), on_event=on_event)
    assert events[0].details["target"] == {
        "id": "fake-target",
        "voice": "fake-voice",
        "language": "en-us",
    }
    assert [event.kind for event in events] == [
        "profile_resolved",
        "cache_scanned",
        "engine_open_started",
        "engine_open_finished",
        "prepare_started",
        "prepare_finished",
        "unit_started",
        "unit_finished",
        "unit_started",
        "unit_finished",
        "activation_started",
        "activation_finished",
        "complete",
    ]
    assert adapter.prepared[0].render_calls == 1
    assert events[6].unit_id == "unit-0000"
    assert events[7].completed == 1
    assert events[7].text == "Alpha."
    assert events[7].details["segment_ids"] == ["seg-000000"]
    assert events[-1].details["rendered"] == 2

    events.clear()
    second = synthesize_project(project, cfg, request=_request(project), on_event=events.append)
    assert second["rendered"] == 0
    assert adapter.open_calls == 1
    assert [event.kind for event in events] == [
        "profile_resolved",
        "cache_scanned",
        "activation_started",
        "activation_finished",
        "complete",
    ]
    events.clear()
    second = synthesize_project(project, cfg, request=_request(project), on_event=events.append)
    assert second["rendered"] == 0
    assert adapter.open_calls == 1
    assert [event.kind for event in events] == [
        "profile_resolved",
        "cache_scanned",
        "activation_started",
        "activation_finished",
        "complete",
    ]


def test_incremental_synthesis_reports_premature_render_exhaustion(tmp_path, monkeypatch):
    adapter = _Adapter(prepared_factory=_prepared_factory(0))
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)

    with pytest.raises(
        ValueError, match=r"engine stopped rendering before plan unit unit-0001 \(index 1\)"
    ):
        synthesize_project(project, cfg, request=_request(project))

    prepared = adapter.prepared[0]
    assert prepared.render_calls == 1
    assert prepared.results[0].audio.size == 0


def test_incremental_synthesis_rejects_out_of_order_rendered_units(tmp_path, monkeypatch):
    adapter = _Adapter(prepared_factory=_prepared_factory(1))
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)

    with pytest.raises(ValueError, match=r"engine returned plan unit index 1; expected 0"):
        synthesize_project(project, cfg, request=_request(project))

    prepared = adapter.prepared[0]
    assert prepared.render_calls == 1
    assert prepared.results[0].audio.size == 0


def test_project_synthesis_merges_document_voice_bindings_with_explicit_overrides(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from readio.stages import synthesis as synthesis_stage

    adapter = _Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "episode.ssmd"
    source.write_text(
        "---\nvoice_bindings:\n  kokoro:\n    narrator: af_heart\n---\n"
        '<div voice="narrator">Hello.</div>\n<div voice="guest">Guest.</div>',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "episode.readio")
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="guest", voice="am_michael"
        ),
    )
    plan_project(project, cfg)
    request = replace(_request(project), voice_bindings={"narrator": "af_sarah"})
    captured = {}

    def resolve(_cfg, resolved_request):
        captured["request"] = resolved_request
        return SimpleNamespace(
            plan=SimpleNamespace(ok=True, diagnostics=()),
            selection=EngineSelection(
                engine="fake",
                target_id="fake-target",
                language="en-us",
                voice="fake-voice",
            ),
        )

    monkeypatch.setattr(synthesis_stage, "resolve_execution_v2", resolve)
    synthesis_stage._resolve_profile(project, cfg, request)

    resolved_request = captured["request"]
    assert resolved_request.input.document.format == "ssmd"
    assert dict(resolved_request.voice_bindings) == {"narrator": "af_sarah"}
    assert dict(resolved_request.project_voice_bindings) == {"guest": "am_michael"}


def test_project_synthesis_forwards_effective_ssmd_voice_binding(tmp_path, monkeypatch):

    from readio.stages import synthesis as synthesis_stage

    adapter = _Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "episode.ssmd"
    source.write_text('<div voice="narrator">Hello.</div>', encoding="utf-8")
    project = init_project(source, tmp_path / "episode.readio")
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="narrator", voice="af_heart"
        ),
    )
    plan_project(project, cfg)
    request = _request(project)

    resolved, _, _ = synthesis_stage._resolve_profile(project, cfg, request)

    assert resolved.selection is not None
    assert resolved.selection.options["ssmd_voice_bindings"] == {
        "narrator": "af_heart",
    }
    decision = next(
        item for item in resolved.plan.decisions if item.field == "ssmd.bindings.narrator"
    )
    assert decision.origin == "project"
    assert decision.locator == "project.settings.ssmd.voice_bindings.kokoro.narrator"


def test_project_synthesis_cli_binding_overrides_project_binding(tmp_path, monkeypatch):
    from dataclasses import replace

    from readio.stages import synthesis as synthesis_stage

    monkeypatch.setitem(_registry._adapters, "fake", _Adapter())
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "episode.ssmd"
    source.write_text('<div voice="narrator">Hello.</div>', encoding="utf-8")
    project = init_project(source, tmp_path / "episode.readio")
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="narrator", voice="af_heart"
        ),
    )
    plan_project(project, cfg)
    request = replace(_request(project), voice_bindings={"narrator": "af_bella"})

    resolved, _, _ = synthesis_stage._resolve_profile(project, cfg, request)

    assert resolved.selection.options["ssmd_voice_bindings"] == {"narrator": "af_bella"}
    decision = next(
        item for item in resolved.plan.decisions if item.field == "ssmd.bindings.narrator"
    )
    assert decision.origin == "cli"


def test_project_synthesis_document_binding_overrides_project_and_cli(tmp_path, monkeypatch):
    from dataclasses import replace

    from readio.stages import synthesis as synthesis_stage

    monkeypatch.setitem(_registry._adapters, "fake", _Adapter())
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "episode.ssmd"
    source.write_text(
        "---\nvoice_bindings:\n  kokoro:\n    narrator: am_michael\n---\n"
        '<div voice="narrator">Hello.</div>',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "episode.readio")
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="narrator", voice="af_heart"
        ),
    )
    plan_project(project, cfg)
    request = replace(_request(project), voice_bindings={"narrator": "af_bella"})

    resolved, _, _ = synthesis_stage._resolve_profile(project, cfg, request)

    assert resolved.selection.options["ssmd_voice_bindings"] == {"narrator": "am_michael"}
    decision = next(
        item for item in resolved.plan.decisions if item.field == "ssmd.bindings.narrator"
    )
    assert decision.origin == "document"


def test_synthesis_preview_and_project_render_share_project_voice_bindings(tmp_path, monkeypatch):
    from readio.stages import pipeline as pipeline_stage

    monkeypatch.setitem(_registry._adapters, "fake", _Adapter())
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "episode.ssmd"
    source.write_text('<div voice="narrator">Hello.</div>', encoding="utf-8")
    project = init_project(source, tmp_path / "episode.readio")
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="narrator", voice="af_heart"
        ),
    )
    plan_project(project, cfg)
    expected_bindings = {"narrator": "af_heart"}

    initial = synthesize_project(project, cfg, request=_request(project))
    assert (
        initial["profile"].payload["canonical"]["options"]["ssmd_voice_bindings"]
        == expected_bindings
    )

    original_synthesize = pipeline_stage.synthesize_project
    observed_bindings = []

    def capture_synthesis(*args, **kwargs):
        result = original_synthesize(*args, **kwargs)
        observed_bindings.append(
            result["profile"].payload["canonical"]["options"]["ssmd_voice_bindings"]
        )
        return result

    monkeypatch.setattr(pipeline_stage, "synthesize_project", capture_synthesis)
    monkeypatch.setattr(pipeline_stage, "compose_artifacts", lambda *args, **kwargs: {})
    pipeline_stage.preview_project(project, cfg, request=_request(project), selector="all")
    assert observed_bindings[-1] == expected_bindings

    monkeypatch.setattr(
        pipeline_stage,
        "build_audio_job",
        lambda *args, **kwargs: (None, {"composition_id": "ready"}),
    )
    master = project.paths["composition_master"]
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_bytes(b"master")
    composition_state = {
        "composition_id": "ready",
        "synthesis_profile_id": initial["profile"].profile_id,
        "master_sha256": hash_file(master),
    }
    project.paths["composition_state"].write_text(json.dumps(composition_state), encoding="utf-8")
    output_path = project.root / "output" / f"{project.manifest.name}.wav"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"output")
    output_state = {
        "audio_format": "wav",
        "master_sha256": hash_file(master),
        "output_sha256": hash_file(output_path),
    }
    (project.root / "output" / "state.json").write_text(json.dumps(output_state), encoding="utf-8")

    pipeline_stage.render_project(project, cfg)

    assert observed_bindings[-1] == expected_bindings


def test_project_request_uses_project_provider_and_suppresses_global_voice(tmp_path):
    from readio.stages.synthesis import _project_request_with_voice_bindings

    source = tmp_path / "episode.ssmd"
    source.write_text('<div voice="narrator">Hello.</div>', encoding="utf-8")
    project = init_project(source, tmp_path / "episode.readio")
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            with_project_voice_provider(manifest, "piper"),
            provider="piper",
            role="narrator",
            voice="en_US-bryce-medium",
        ),
    )
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="af_sarah"))
    request = PlanRequest(
        "render",
        InputRequest(project.load_document_scope(project.document_scopes()[0])),
        SynthesisRequest(),
    )

    resolved = _project_request_with_voice_bindings(project, cfg, request)

    assert resolved.synthesis.engine == "piper"
    assert resolved.synthesis.voice is None
    assert dict(resolved.project_voice_bindings) == {"narrator": "en_US-bryce-medium"}
    assert resolved.scope_voice_bindings == {"document": {"narrator": "en_US-bryce-medium"}}


def test_project_request_engine_override_uses_override_provider_without_mutating_project(
    tmp_path,
):
    from readio.stages.synthesis import _project_request_with_voice_bindings

    source = tmp_path / "episode.ssmd"
    source.write_text('<div voice="narrator">Hello.</div>', encoding="utf-8")
    project = init_project(source, tmp_path / "episode.readio")
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            with_project_voice_provider(manifest, "piper"),
            provider="piper",
            role="narrator",
            voice="en_US-bryce-medium",
        ),
    )
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="af_sarah"))
    request = PlanRequest(
        "render",
        InputRequest(project.load_document_scope(project.document_scopes()[0])),
        SynthesisRequest(engine="pykokoro"),
        voice_bindings={"narrator": "af_heart"},
    )

    resolved = _project_request_with_voice_bindings(project, cfg, request)

    assert resolved.synthesis.engine == "pykokoro"
    assert resolved.synthesis.voice is None
    assert dict(resolved.project_voice_bindings) == {}
    assert resolved.scope_voice_bindings == {"document": {"narrator": "af_heart"}}
    assert project.manifest.settings["ssmd"]["voice_provider"] == "piper"


def test_project_request_preserves_global_defaults_without_project_provider_state(
    tmp_path, monkeypatch
):
    from readio.stages.synthesis import _project_request_with_voice_bindings

    source = tmp_path / "episode.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "episode.readio")
    monkeypatch.setitem(_registry._adapters, "fake", _Adapter())
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    request = PlanRequest(
        "render",
        InputRequest(project.load_document_scope(project.document_scopes()[0])),
        SynthesisRequest(),
    )

    resolved = _project_request_with_voice_bindings(project, cfg, request)

    assert resolved.synthesis.engine == "fake"
    assert resolved.synthesis.voice == "fake-voice"


def test_project_request_resolves_document_bindings_per_scope(tmp_path, monkeypatch):
    from readio.stages.synthesis import _project_request_with_voice_bindings

    source = tmp_path / "episode.ssmd"
    source.write_text("Unused.", encoding="utf-8")
    project = init_project(source, tmp_path / "episode.readio")
    scopes = (
        DocumentScope(
            id="chapter-0001",
            kind="chapter",
            path="document/chapters/chapter-0001.ssmd",
            input_format="ssmd",
        ),
        DocumentScope(
            id="chapter-0002",
            kind="chapter",
            path="document/chapters/chapter-0002.ssmd",
            input_format="ssmd",
        ),
    )
    for scope, voice in zip(scopes, ("af_sarah", "am_michael")):
        path = project.path(scope.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nvoice_bindings:\n  kokoro:\n    narrator: {voice}\n---\n"
            '<div voice="narrator">Hello.</div>',
            encoding="utf-8",
        )
    project.paths["document_index"].write_text(
        json.dumps(DocumentIndex(scopes=scopes).to_dict()), encoding="utf-8"
    )
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="narrator", voice="af_heart"
        ),
    )
    monkeypatch.setitem(_registry._adapters, "fake", _Adapter())
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    request = PlanRequest(
        "render",
        InputRequest(project.load_document_scope(scopes[0])),
        SynthesisRequest(engine="fake"),
    )

    resolved = _project_request_with_voice_bindings(project, cfg, request)

    assert resolved.scope_voice_bindings == {
        "chapter-0001": {"narrator": "af_sarah"},
        "chapter-0002": {"narrator": "am_michael"},
    }


def test_pipeline_project_request_does_not_inject_global_engine_or_voice(tmp_path):
    from readio.stages.pipeline import _project_request

    source = tmp_path / "episode.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "episode.readio")
    cfg = ReadioConfig(reader=ReaderSettings(engine="pykokoro", voice="af_sarah"))

    request = _project_request(project, cfg)

    assert request.synthesis.engine is None
    assert request.synthesis.voice is None
