from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from readio.api import (
    InvalidRequestError,
    ProjectRole,
    Readio,
    RoleBinding,
    SSMDAnalysis,
    SSMDCheckResult,
    SSMDMaterializeResult,
    default_config,
    document_from_text,
    register_engine,
)


def test_public_engine_extension_discovers_plans_and_renders(tmp_path: Path) -> None:
    output = tmp_path / "extension.wav"
    script = r'''
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import numpy as np
from audiocompose import AudioBufferSource, AudioClip, AudioJob
from readio.api import (
    EngineCapabilities, EngineSelection, InputRequest, ModelQuery, OutputRequest,
    PlanRequest, Readio, SynthesisRequest, TargetQuery, VoiceQuery, default_config,
    document_from_text, register_engine, registered_engines,
)

ENGINE_ID = "api-extension-fixture"

class Session:
    def __init__(self, adapter):
        self.adapter = adapter
    def to_audio_job(self, plan, *, options):
        unit = plan.units[0]
        clip = AudioClip(
            id=unit.id,
            source=AudioBufferSource(np.ones(16, dtype=np.float32), 24000),
            metadata={"plan_unit_id": unit.id},
        )
        return AudioJob(items=(clip,))

class Adapter:
    id = ENGINE_ID
    def __init__(self):
        self.discover_calls = 0
        self.open_calls = 0
        self.options = None
    def version(self):
        return "1.0"
    def capabilities(self):
        return EngineCapabilities(id=self.id, option_names=frozenset({"color"}))
    def discover(self, request):
        self.discover_calls += 1
        from readio.api import SynthesisTarget
        return (SynthesisTarget(engine=self.id, id="fixture", display_name="Fixture", voices=("fixture-voice",)),)
    def resolve(self, request):
        self.options = dict(request.options)
        self.options.update(dict(request.engine_options))
        return EngineSelection(
            engine=self.id, target_id=request.target_id or "fixture",
            language=request.language or "en-us", voice=request.voice,
            options=self.options,
        ), ()
    def planner_config(self, selection, planning):
        return None
    def canonical_synthesis_identity(self, selection):
        return {"engine": self.id, "target": selection.target_id, "options": dict(selection.options)}
    def open(self, selection):
        self.open_calls += 1
        @contextmanager
        def session():
            yield Session(self)
        return session()

adapter = Adapter()
register_engine(adapter)
app = Readio(default_config())
targets = app.catalog.targets(TargetQuery(engine=ENGINE_ID))
assert len(targets) == 1 and targets[0].id == "fixture"
models = app.catalog.models(ModelQuery(engine=ENGINE_ID))
assert len(models) == 1 and models[0].backend == ENGINE_ID
voices = app.catalog.voices(VoiceQuery(engine=ENGINE_ID))
assert len(voices) == 1 and voices[0].id == "fixture-voice"
assert ENGINE_ID in registered_engines()
info = next(item for item in app.catalog.engines() if item.id == ENGINE_ID)
assert info.registered and info.runnable and info.capabilities.id == ENGINE_ID
request = PlanRequest(
    operation="render",
    input=InputRequest(document=document_from_text("Public engine API.")),
    synthesis=SynthesisRequest(
        engine=ENGINE_ID, voice="fixture-voice", engine_options={"color": "warm"}
    ),
    output=OutputRequest(mode="file", requested_path=Path(__import__("sys").argv[1])),
)
plan = app.speech.plan(request)
assert plan.ok
assert adapter.options["color"] == "warm"
bad_request = replace(
    request,
    synthesis=replace(request.synthesis, engine_options={"unsupported": True}),
)
bad_plan = app.speech.plan(bad_request)
assert not bad_plan.ok
assert any(diagnostic.code == "engine_option_unsupported" for diagnostic in bad_plan.diagnostics)
result = app.speech.render(request)
assert result.output_path and result.output_path.is_file()
assert adapter.open_calls == 1
'''
    completed = subprocess.run(
        [sys.executable, "-c", script, str(output)],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_global_roles_persist_and_return_typed_bindings(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "readio.toml"
    monkeypatch.setenv("READIO_CONFIG", str(config_path))
    app = Readio(default_config())

    binding = app.roles.bind_global("api_test", "af_heart")

    assert isinstance(binding, RoleBinding)
    assert binding.provider == "kokoro"
    assert binding.voice == "af_heart"
    assert "api_test" not in app.config.voices["kokoro"].roles
    assert "api_test" in config_path.read_text(encoding="utf-8")
    reloaded = Readio()
    assert any(item.role == "api_test" for item in reloaded.roles.list_global())

    reloaded.roles.unbind_global("api_test")
    assert "api_test" in reloaded.config.voices["kokoro"].roles
    assert all(item.role != "api_test" for item in Readio().roles.list_global())


def test_project_role_operations_preserve_effective_binding(tmp_path: Path) -> None:
    source = tmp_path / "roles.ssmd"
    source.write_text(
        '---\ntitle: Roles\n---\n\n<div voice="api_speaker">Hello.</div>\n',
        encoding="utf-8",
    )
    app = Readio(default_config())
    project = app.projects.create(source, output=tmp_path / "roles.readio")

    before = app.roles.inspect_project(project)
    assert before.unresolved == ("api_speaker",)

    bound = app.roles.bind_project(project, "api_speaker", "af_heart")

    assert isinstance(bound, ProjectRole)
    assert bound.effective_voice == "af_heart"
    assert bound.origin == "project"
    assert app.roles.inspect_project(project).unresolved == ()

    app.roles.unbind_project(project, "api_speaker")
    assert app.roles.inspect_project(project).unresolved == ("api_speaker",)


def test_ssmd_service_checks_materializes_and_roundtrips(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "input.ssmd"
    source.write_text(
        '---\nvoice_bindings:\n  kokoro:\n    speaker: missing_voice\n---\n'
        '<div voice="speaker">Hello.</div>\n',
        encoding="utf-8",
    )
    app = Readio(default_config())

    analysis = app.ssmd.analyze(source)
    assert isinstance(analysis, SSMDAnalysis)
    assert analysis.source_path == source
    assert analysis.unresolved_references == ()
    assert any(item.code == "ssmd.document_binding_invalid" for item in analysis.diagnostics)

    monkeypatch.setattr(
        "readio.api.ssmd.ssmd_authoring.materialize_voice_bindings",
        lambda source, bindings, **kwargs: kwargs["output"] or source,
    )
    materialized = app.ssmd.materialize_bindings(
        source, {"speaker": "af_heart"}, output=tmp_path / "materialized.ssmd"
    )
    assert isinstance(materialized, SSMDMaterializeResult)
    assert materialized.output_path == tmp_path / "materialized.ssmd"
    assert materialized.binding_count == 1

    monkeypatch.setattr(
        "readio.api.ssmd.ssmd_authoring.roundtrip_check",
        lambda source, config: {"ok": True, "checked": str(source)},
    )
    checked = app.ssmd.check(source, roundtrip=True)
    assert isinstance(checked, SSMDCheckResult)
    assert checked.roundtrip and checked.roundtrip["ok"] is True
    assert not checked.ok
    json.dumps(checked.to_dict())


def test_ssmd_service_rejects_non_ssmd_documents() -> None:
    app = Readio(default_config())
    with pytest.raises(InvalidRequestError) as error:
        app.ssmd.analyze(document_from_text("plain text"))
    assert error.value.code == "ssmd.input_format_required"


def test_catalog_audio_formats_are_typed() -> None:
    from readio.api import AudioFormatInfo

    formats = Readio(default_config()).catalog.audio_formats()
    assert formats
    assert all(isinstance(item, AudioFormatInfo) for item in formats)


def test_engine_registration_rejects_invalid_contract_with_public_errors() -> None:
    class InvalidId:
        id = "Invalid"

    with pytest.raises(InvalidRequestError) as invalid_id:
        register_engine(InvalidId())
    assert invalid_id.value.code == "engine.invalid_id"

    class IncompleteAdapter:
        id = "incomplete"

    with pytest.raises(InvalidRequestError) as incomplete:
        register_engine(IncompleteAdapter())
    assert incomplete.value.code == "engine.contract_incomplete"
