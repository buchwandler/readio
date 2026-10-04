from __future__ import annotations

from contextlib import contextmanager
from typing import ClassVar

import numpy as np
import pytest

from readio.config import LanguageSettings, ReaderSettings, ReadioConfig, normalize_language_key
from readio.engines.base import EngineCapabilities, EngineSelection, RenderedSpeech
from readio.engines.registry import _registry
from readio.plan import (
    InputRequest,
    OutputRequest,
    PlanDiagnostic,
    PlanRequest,
    SynthesisRequest,
)
from readio.project import init_project, update_project_manifest
from readio.project_settings import (
    with_project_role_binding,
)
from readio.role_targets import VoiceTarget
from readio.stages.composition import _cache_entries
from readio.stages.pipeline import _synthesis_status
from readio.stages.planning import load_scope_plan, plan_project
from readio.stages.synthesis import synthesize_project


def _with_engine_role(manifest, *, engine: str, role: str, voice: str):
    return with_project_role_binding(manifest, role=role, target=VoiceTarget(engine, voice))


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

    def target_metadata(self, selection):
        return {"voices": (selection.target_id,)}

    def validate_selection(self, selection):
        if selection.target_id not in self.target_ids:
            return (
                PlanDiagnostic(
                    code="piper.voice_not_found",
                    severity="error",
                    message=f"Piper target {selection.target_id!r} is unavailable.",
                ),
            )
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


class _KokoroTargetAdapter(_PiperTargetAdapter):
    id = "kokoro"
    target_ids = ("kokoro-alice", "kokoro-bob")

    def version(self):
        return "fixture-kokoro"

    def capabilities(self):
        return EngineCapabilities(
            id=self.id,
            voice_binding_namespace="kokoro",
            voice_binding_scope="target",
            supports_named_voices=True,
        )


class _MultilingualTargetAdapter(_PiperTargetAdapter):
    id = "localized"
    target_ids = ("model-en", "model-de")
    target_languages: ClassVar[dict[str, str]] = {"model-en": "en-us", "model-de": "de-de"}

    def __init__(self):
        super().__init__()
        self.resolve_calls = []

    def resolve(self, request):
        self.resolve_calls.append((request.language, request.target_id))
        return super().resolve(request)

    def validate_selection(self, selection):
        diagnostics = super().validate_selection(selection)
        if diagnostics:
            return diagnostics
        expected_language = self.target_languages[selection.target_id]
        if normalize_language_key(selection.language) != expected_language:
            return (
                PlanDiagnostic(
                    code="localized.language_incompatible",
                    severity="error",
                    message=(
                        f"Target {selection.target_id!r} is incompatible with "
                        f"language {selection.language!r}."
                    ),
                ),
            )
        return ()


def test_project_routes_roles_to_distinct_target_sessions_without_replanning_semantics(
    tmp_path, monkeypatch
):
    adapter = _PiperTargetAdapter()
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    cfg = ReadioConfig(
        reader=ReaderSettings(engine=adapter.id, voice=adapter.target_ids[0], spacy="off"),
        roles={},
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
        manifest = _with_engine_role(
            manifest, engine=adapter.id, role="host", voice=adapter.target_ids[0]
        )
        return _with_engine_role(
            manifest, engine=adapter.id, role="guest", voice=adapter.target_ids[1]
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

    invalid_project = update_project_manifest(
        project,
        lambda manifest: _with_engine_role(
            manifest,
            engine=adapter.id,
            role="guest",
            voice="missing-target",
        ),
    )
    with pytest.raises(ValueError, match="unavailable target 'missing-target'"):
        synthesize_project(invalid_project, cfg, request=request)

    project = update_project_manifest(
        project,
        lambda manifest: _with_engine_role(
            manifest,
            engine=adapter.id,
            role="guest",
            voice=adapter.target_ids[0],
        ),
    )
    plan_project(project, cfg)

    assert project.load_plan_index().scopes[0].plan_id == semantic_plan_id


def test_mixed_engine_project_routes_and_reuses_route_local_cache(tmp_path, monkeypatch):
    kokoro = _KokoroTargetAdapter()
    piper = _PiperTargetAdapter()
    monkeypatch.setitem(_registry._adapters, kokoro.id, kokoro)
    monkeypatch.setitem(_registry._adapters, piper.id, piper)
    cfg = ReadioConfig(
        reader=ReaderSettings(engine=kokoro.id, voice=kokoro.target_ids[0], spacy="off"),
        roles={},
    )
    source = tmp_path / "mixed.ssmd.md"
    source.write_text(
        '---\nssmd_version: "0.9"\n---\n'
        ':::{voice="guest"}\nPiper line.\n:::\n'
        ':::{voice="host"}\nKokoro line.\n:::\n'
        ':::{voice="host"}\nAnother Kokoro line.\n:::\n',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "mixed.readio")

    def bind_roles(manifest):
        manifest = with_project_role_binding(
            manifest,
            role="host",
            target=VoiceTarget(engine=kokoro.id, voice=kokoro.target_ids[0]),
        )
        return with_project_role_binding(
            manifest,
            role="guest",
            target=VoiceTarget(engine=piper.id, voice=piper.target_ids[0]),
        )

    project = update_project_manifest(project, bind_roles)
    plan_project(project, cfg)
    semantic_plan_id = project.load_plan_index().scopes[0].plan_id
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=project.document()),
        synthesis=SynthesisRequest(),
        output=OutputRequest(mode="file", requested_format="wav", force=True),
    )

    first = synthesize_project(project, cfg, request=request)
    assert first["rendered"] == 3
    assert first["reused"] == 0
    assert len(kokoro.open_calls) == len(piper.open_calls) == 1

    second = synthesize_project(project, cfg, request=request)
    assert second["rendered"] == 0
    assert second["reused"] == 3
    initial_profile = first["profile"]
    initial_routes = initial_profile.payload["canonical"]["routes"]
    initial_route_ids = initial_profile.payload["canonical"]["route_profile_ids"]
    kokoro_route_key = next(
        key
        for key, identity in initial_routes.items()
        if identity["target_id"] == kokoro.target_ids[0]
    )
    kokoro_route_profile_id = initial_route_ids[kokoro_route_key]
    assert second["profile"].profile_id == initial_profile.profile_id

    project = update_project_manifest(
        project,
        lambda manifest: with_project_role_binding(
            manifest,
            role="guest",
            target=VoiceTarget(engine=piper.id, voice=piper.target_ids[1]),
        ),
    )
    plan_project(project, cfg)
    assert project.load_plan_index().scopes[0].plan_id == semantic_plan_id
    changed = synthesize_project(project, cfg, request=request)

    changed_profile = changed["profile"]
    changed_canonical = changed_profile.payload["canonical"]
    assert changed_profile.profile_id != initial_profile.profile_id
    assert changed_canonical["route_profile_ids"][kokoro_route_key] == kokoro_route_profile_id
    assert changed["rendered"] == 1
    assert changed["reused"] == 2
    assert len(kokoro.open_calls) == 1
    assert piper.open_calls == list(piper.target_ids)
    assert _synthesis_status(project)["state"] == "current"

    scope = project.load_plan_index().scopes[0]
    plan = load_scope_plan(project, scope)
    assert len(_cache_entries(project, plan, scope.id)) == 3


def _multilingual_config(adapter: _MultilingualTargetAdapter) -> ReadioConfig:
    return ReadioConfig(
        reader=ReaderSettings(engine=adapter.id, lang="fr-fr", voice=None, spacy="off"),
        languages={
            "en-us": LanguageSettings(engine=adapter.id, model="model-en"),
            "de-de": LanguageSettings(engine=adapter.id, model="model-de"),
        },
        roles={},
    )


def _multilingual_project(tmp_path):
    source = tmp_path / "languages.ssmd.md"
    source.write_text(
        '---\nssmd_version: "0.9"\nlanguage: en-US\n---\n'
        '[Guten Tag]{lang="de-DE"}. English sentence.\n',
        encoding="utf-8",
    )
    return init_project(source, tmp_path / "languages.readio")


def test_semantic_languages_select_language_profiles_before_each_route(tmp_path, monkeypatch):
    adapter = _MultilingualTargetAdapter()
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    cfg = _multilingual_config(adapter)
    project = _multilingual_project(tmp_path)
    plan_project(project, cfg)
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=project.document()),
        synthesis=SynthesisRequest(),
        output=OutputRequest(mode="file", requested_format="wav", force=True),
    )

    result = synthesize_project(project, cfg, request=request)

    assert result["rendered"] >= 2
    assert adapter.resolve_calls[0] == ("de-de", "model-de")
    assert ("en-us", "model-en") in adapter.resolve_calls
    assert set(adapter.open_calls) == {"model-en", "model-de"}


def test_explicit_model_incompatible_with_semantic_language_fails(tmp_path, monkeypatch):
    adapter = _MultilingualTargetAdapter()
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    cfg = _multilingual_config(adapter)
    project = _multilingual_project(tmp_path)
    plan_project(project, cfg)
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=project.document()),
        synthesis=SynthesisRequest(model="model-en"),
        output=OutputRequest(mode="file", requested_format="wav", force=True),
    )

    with pytest.raises(ValueError, match="incompatible with language"):
        synthesize_project(project, cfg, request=request)
