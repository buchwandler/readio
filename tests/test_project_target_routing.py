from __future__ import annotations

from contextlib import contextmanager
from typing import ClassVar

import numpy as np
import pytest

from readio.config import LanguageSettings, ReaderSettings, ReadioConfig, normalize_language_key
from readio.engines.api_probe import EngineApiProbe
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
from readio.stages.pipeline import _synthesis_status
from readio.stages.planning import plan_project
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

    def probe_api(self):
        return EngineApiProbe(
            engine=self.id,
            package=f"{self.id}-fixture",
            compatible=True,
            status="ready",
        )

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


def _install_voicerender_fakes(monkeypatch):
    import hashlib
    import json

    from utterplan import FlowPlan
    from voicerender import EngineDiagnostic, RenderEvent, RenderEventKind, RenderResult

    from readio.integrations import voicerender as voicerender_integration
    from readio.integrations.voicerender import RenderBundle
    from readio.stages import pipeline as pipeline_stage
    from readio.stages import synthesis as synthesis_stage

    resolutions = []
    rendered_routes = []
    rendered_cache = set()

    def resolve_voice(request):
        adapter = _registry._adapters[request.engine]
        selection, diagnostics = adapter.resolve(request)
        diagnostics = (*diagnostics, *adapter.validate_selection(selection))
        public_diagnostics = tuple(
            EngineDiagnostic(
                code=item.code,
                severity=item.severity,
                message=item.message,
            )
            for item in diagnostics
        )
        resolutions.append((request.language, request.engine, selection.target_id))
        return selection, public_diagnostics

    def render_plan(
        source_plan,
        *,
        profile,
        output_dir,
        cache_dir,
        locators,
        on_event,
        **_kwargs,
    ):
        del cache_dir
        plan = FlowPlan.load(source_plan)
        role_identities = {
            role: {
                "engine": binding.engine,
                "target_id": binding.target_id,
                "voice": binding.voice,
                "speaker": binding.speaker,
                "options": dict(binding.options),
            }
            for role, binding in profile.roles.items()
        }
        profile_payload = json.dumps(role_identities, sort_keys=True, separators=(",", ":"))
        profile_hash = "sha256:" + hashlib.sha256(profile_payload.encode()).hexdigest()
        manifest_path = output_dir / "render-manifest.json"
        output_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        rendered_count = 0
        reused_count = 0
        for locator in locators:
            flow_index, segment_index = map(int, locator[1:].split(".s"))
            segment = plan.flow[flow_index].segments[segment_index]
            voice = segment.directives.voice
            role = getattr(voice, "reference", None) or "narrator"
            binding = profile.roles[role]
            route_payload = json.dumps(
                {
                    "engine": binding.engine,
                    "target_id": binding.target_id,
                    "voice": binding.voice,
                    "speaker": binding.speaker,
                    "options": dict(binding.options),
                    "text": segment.text,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            route_hash = hashlib.sha256(route_payload.encode()).hexdigest()
            cache_key = (locator, route_hash)
            reused = cache_key in rendered_cache
            rendered_cache.add(cache_key)
            status = "reused" if reused else "rendered"
            reused_count += int(reused)
            rendered_count += int(not reused)
            rendered_routes.append((role, binding.engine, binding.target_id, binding.voice, segment.text, status))
            if on_event is not None:
                on_event(RenderEvent(RenderEventKind.SEGMENT_STARTED, plan_id=plan.plan_id, locator=locator))
                event_kind = RenderEventKind.SEGMENT_REUSED if reused else RenderEventKind.SEGMENT_RENDERED
                on_event(RenderEvent(event_kind, plan_id=plan.plan_id, locator=locator, status=status))
            semantic_hash = hashlib.sha256(segment.text.encode()).hexdigest()
            audio_relative = f"segments/{locator.replace('.', '-')}.wav"
            audio_path = output_dir / audio_relative
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(segment.text.encode())
            entries.append(
                {
                    "source": {
                        "locator": locator,
                        "semantic_segment_hash": "sha256:" + semantic_hash,
                    },
                    "status": status,
                    "acoustic_cache_key": "sha256:" + route_hash,
                    "synthesis_semantics_sha256": "sha256:" + route_hash,
                    "audio": {
                        "path": audio_relative,
                        "sha256": semantic_hash,
                        "sample_rate": 22050,
                        "channels": 1,
                        "frames": 64,
                        "encoding": "PCM_16",
                    },
                    "timing": {"spans": []},
                    "text": segment.text,
                }
            )
        manifest = {
            "manifest_id": "sha256:" + hashlib.sha256(
                (profile_hash + "|" + "|".join(locators)).encode()
            ).hexdigest(),
            "source": {
                "plan_id": plan.plan_id,
                "plan_file_sha256": "sha256:" + hashlib.sha256(source_plan.read_bytes()).hexdigest(),
            },
            "profile": {"effective_profile_sha256": profile_hash},
            "segments": entries,
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return RenderBundle(
            RenderResult(
                manifest_path=manifest_path,
                rendered_count=rendered_count,
                reused_count=reused_count,
                failed_count=0,
                selected_count=len(entries),
            ),
            manifest,
        )

    monkeypatch.setattr(voicerender_integration, "resolve_voice", resolve_voice)
    monkeypatch.setattr(synthesis_stage, "voicerender_render_plan", render_plan)
    monkeypatch.setattr(
        pipeline_stage,
        "read_render_manifest",
        lambda path: json.loads(path.read_text(encoding="utf-8")),
    )
    return resolutions, rendered_routes


def test_project_routes_roles_to_distinct_target_sessions_without_replanning_semantics(
    tmp_path, monkeypatch
):
    adapter = _PiperTargetAdapter()
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    resolutions, rendered_routes = _install_voicerender_fakes(monkeypatch)
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
    assert {
        (role, target_id, text)
        for role, _engine, target_id, _voice, text, status in rendered_routes
        if status == "rendered"
    } == {
        ("host", adapter.target_ids[0], "First line."),
        ("guest", adapter.target_ids[1], "Second line."),
        ("host", adapter.target_ids[0], "Third line."),
    }
    assert {(engine, target_id) for _language, engine, target_id in resolutions} == {
        (adapter.id, target) for target in adapter.target_ids
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
    with pytest.raises(ValueError, match="Piper target 'missing-target' is unavailable"):
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
    _resolutions, rendered_routes = _install_voicerender_fakes(monkeypatch)
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

    second = synthesize_project(project, cfg, request=request)
    assert second["rendered"] == 0
    assert second["reused"] == 3
    initial_profile = first["profile"]
    initial_ref = initial_profile.payload["manifest_refs"][0]
    assert second["profile"].profile_id == initial_profile.profile_id
    assert second["profile"].payload["manifest_refs"][0] == initial_ref
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
    assert changed_profile.profile_id != initial_profile.profile_id
    changed_ref = changed_profile.payload["manifest_refs"][0]
    assert changed_profile.profile_id != initial_profile.profile_id
    assert changed_ref["effective_profile_sha256"] != initial_ref["effective_profile_sha256"]
    assert changed["rendered"] == 1
    assert changed["reused"] == 2
    assert [(route[0], route[5]) for route in rendered_routes[-3:]] == [
        ("guest", "rendered"),
        ("host", "reused"),
        ("host", "reused"),
    ]
    assert _synthesis_status(project)["state"] == "current"



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
        '[Guten Tag]{lang="de-DE"} English sentence\n',
        encoding="utf-8",
    )
    return init_project(source, tmp_path / "languages.readio")


def test_semantic_languages_select_language_profiles_before_each_route(tmp_path, monkeypatch):
    adapter = _MultilingualTargetAdapter()
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    resolutions, _rendered_routes = _install_voicerender_fakes(monkeypatch)
    cfg = _multilingual_config(adapter)
    project = _multilingual_project(tmp_path)
    plan_project(project, cfg)
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=project.document()),
        synthesis=SynthesisRequest(),
        output=OutputRequest(mode="file", requested_format="wav", force=True),
    )

    events = []
    result = synthesize_project(project, cfg, request=request, on_event=events.append)

    started = [event for event in events if event.kind == "segment_started"]
    finished = [event for event in events if event.kind == "segment_finished"]
    assert result["rendered"] >= 2
    assert [(event.completed, event.total) for event in started] == [
        (index, result["rendered"]) for index in range(result["rendered"])
    ]
    assert [(event.completed, event.total) for event in finished] == [
        (index, result["rendered"]) for index in range(1, result["rendered"] + 1)
    ]
    assert normalize_language_key(adapter.resolve_calls[0][0]) == "de-de"
    assert adapter.resolve_calls[0][1] == "model-de"
    assert any(
        normalize_language_key(language) == "en-us" and target_id == "model-en"
        for language, target_id in adapter.resolve_calls
    )
    assert {target_id for _language, _engine, target_id in resolutions} == {
        "model-en",
        "model-de",
    }


def test_explicit_model_incompatible_with_semantic_language_fails(tmp_path, monkeypatch):
    adapter = _MultilingualTargetAdapter()
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    _install_voicerender_fakes(monkeypatch)
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
