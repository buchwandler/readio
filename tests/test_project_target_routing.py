from __future__ import annotations

from contextlib import contextmanager

import numpy as np

from readio.config import ReaderSettings, ReadioConfig, VoiceProviderSettings
from readio.engines.base import EngineCapabilities, EngineSelection, RenderedSpeech
from readio.engines.registry import _registry
from readio.plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest
from readio.project import init_project, update_project_manifest
from readio.project_settings import with_project_voice_binding, with_project_voice_provider
from readio.stages.planning import plan_project
from readio.stages.synthesis import synthesize_project


class _TargetSession:
    def __init__(self, adapter, selection):
        self.adapter = adapter
        self.selection = selection

    def synthesize(self, request):
        self.adapter.requests.append((self.selection.target_id, request.voice, request.text))
        return RenderedSpeech(
            id=request.id,
            audio=np.full(64, 0.1, dtype=np.float32),
            sample_rate=22050,
        )


class _PiperTargetAdapter:
    id = "piper"
    target_ids = ("en_US-amy-medium", "en_US-lessac-medium")

    def __init__(self):
        self.open_calls = []
        self.close_calls = []
        self.requests = []

    def version(self):
        return "fixture-piper"

    def capabilities(self):
        return EngineCapabilities(
            id=self.id,
            voice_binding_namespace=self.id,
            voice_binding_scope="target",
            supports_named_voices=True,
        )

    def resolve(self, request):
        voice = request.voice
        target_id = request.target_id or voice or self.target_ids[0]
        return (
            EngineSelection(
                engine=self.id,
                target_id=target_id,
                language=request.language or "en-us",
                voice=voice,
                options=dict(request.options),
            ),
            (),
        )

    def target_metadata(self, _selection):
        return {"voices": self.target_ids}

    def validate_selection(self, selection):
        if selection.target_id not in self.target_ids:
            raise AssertionError(f"unexpected Piper target: {selection.target_id}")
        return ()

    def canonical_synthesis_identity(self, selection):
        return {"engine": self.id, "target_id": selection.target_id, "voice": selection.voice}

    def open(self, selection):
        self.open_calls.append(selection.target_id)
        adapter = self

        @contextmanager
        def session():
            try:
                yield _TargetSession(adapter, selection)
            finally:
                adapter.close_calls.append(selection.target_id)

        return session()


def test_project_routes_roles_to_distinct_target_sessions_without_replanning_semantics(
    tmp_path, monkeypatch
):
    adapter = _PiperTargetAdapter()
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    cfg = ReadioConfig(
        reader=ReaderSettings(engine=adapter.id, voice=adapter.target_ids[0], spacy="off"),
        voices={"piper": VoiceProviderSettings(ids=adapter.target_ids)},
    )
    source = tmp_path / "cast.ssmd.md"
    source.write_text(
        '---\nssmd_version: "0.9"\n---\n'
        ':::{voice="host"}\nFirst line.\n:::\n'
        ':::{voice="guest"}\nSecond line.\n:::\n'
        ':::{voice="host"}\nThird line.\n:::\n',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "cast.readio")

    def bind_roles(manifest):
        manifest = with_project_voice_provider(manifest, adapter.id)
        manifest = with_project_voice_binding(
            manifest, provider=adapter.id, role="host", voice=adapter.target_ids[0]
        )
        return with_project_voice_binding(
            manifest, provider=adapter.id, role="guest", voice=adapter.target_ids[1]
        )

    project = update_project_manifest(project, bind_roles)
    plan_project(project, cfg)
    semantic_plan_id = project.load_plan_index().scopes[0].plan_id
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=project.document()),
        synthesis=SynthesisRequest(engine=adapter.id),
        output=OutputRequest(mode="file", requested_format="wav", force=True),
    )

    result = synthesize_project(project, cfg, request=request)

    assert result["rendered"] == 3
    assert adapter.open_calls == sorted(adapter.target_ids)
    assert adapter.close_calls == sorted(adapter.target_ids)
    assert len(adapter.requests) == 3
    by_target = {target: [] for target in adapter.target_ids}
    for target, voice, text in adapter.requests:
        by_target[target].append((voice, text))
    assert by_target == {
        adapter.target_ids[0]: [
            (adapter.target_ids[0], "First line."),
            (adapter.target_ids[0], "Third line."),
        ],
        adapter.target_ids[1]: [(adapter.target_ids[1], "Second line.")],
    }

    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest,
            provider=adapter.id,
            role="guest",
            voice=adapter.target_ids[0],
        ),
    )
    plan_project(project, cfg)

    assert project.load_plan_index().scopes[0].plan_id == semantic_plan_id
