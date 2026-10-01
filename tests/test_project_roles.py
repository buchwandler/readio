from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from readio.config import ReadioConfig
from readio.project import init_project, load_project, update_project_manifest
from readio.project_model import DocumentIndex, DocumentScope
from readio.project_roles import (
    ProjectRoleError,
    bind_project_role,
    inspect_project_roles,
    unbind_project_role,
)
from readio.project_settings import (
    ProjectVoiceProviderError,
    project_role_bindings,
    project_voice_binding_providers,
    project_voice_bindings,
    project_voice_bindings_provenance,
    resolve_project_voice_provider,
    with_project_role_binding,
    with_project_voice_binding,
    with_project_voice_provider,
    without_project_role_binding,
    without_project_voice_binding,
)
from readio.role_targets import VoiceTarget


def _project(tmp_path, text: str):
    source = tmp_path / "episode.ssmd"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(text, encoding="utf-8")
    return init_project(source, tmp_path / "episode.readio")


def _roles(project, cfg=None):
    inspection = inspect_project_roles(project, cfg or ReadioConfig())
    return {role.role: role for role in inspection.roles}


def test_project_voice_binding_provenance_is_order_independent() -> None:
    first = project_voice_bindings_provenance(
        "kokoro", {"narrator": "af_heart", "guest": "af_bella"}
    )
    second = project_voice_bindings_provenance(
        "kokoro", {"guest": "af_bella", "narrator": "af_heart"}
    )

    assert first == second
    assert first["bindings"] == {"guest": "af_bella", "narrator": "af_heart"}


def test_discovers_source_roles_and_counts_before_semantic_plan_exists(tmp_path) -> None:
    expected_counts = {"narrator": 9, "host": 9, "guest": 11}
    lines = [
        f'[Line {index}.]{{voice="{role}"}}'
        for role, count in expected_counts.items()
        for index in range(count)
    ]
    project = _project(tmp_path, "\n".join(lines))
    project.paths["source"].write_text('[Only current source.]{voice="guest"}', encoding="utf-8")
    assert not project.paths["plan_index"].exists()

    roles = _roles(project)

    assert {name: role.uses for name, role in roles.items()} == {"guest": 1}
    assert roles["guest"].locations[0].scope_id == "document"
    assert roles["guest"].locations[0].lines == (1,)
    assert roles["guest"].origin == "config.voice_role"


def test_collects_all_sample_role_counts_and_config_fallback(tmp_path) -> None:
    expected_counts = {"narrator": 9, "host": 9, "guest": 11}
    lines = [
        f'[Line {index}.]{{voice="{role}"}}'
        for role, count in expected_counts.items()
        for index in range(count)
    ]
    project = _project(tmp_path, "\n".join(lines))

    roles = _roles(project)

    assert {name: role.uses for name, role in roles.items()} == expected_counts
    assert roles["narrator"].effective_voice == "af_sarah"
    assert roles["narrator"].origin == "config.voice_role"
    assert roles["guest"].effective_voice == "af_bella"
    assert roles["guest"].locations[0].lines == tuple(range(19, 30))


def test_project_binding_overrides_config_role(tmp_path) -> None:
    project = _project(tmp_path, '[Hello.]{voice="narrator"}')
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="narrator", voice="af_heart"
        ),
    )

    narrator = _roles(project)["narrator"]

    assert narrator.effective_voice == "af_heart"
    assert narrator.origin == "project"
    assert narrator.project_binding == "af_heart"
    assert narrator.config_binding == "af_sarah"


def test_document_binding_overrides_project_binding(tmp_path) -> None:
    text = (
        "---\nssmd_version: '0.9'\nvoice_bindings:\n  kokoro:\n    narrator: af_bella\n---\n"
        '[Hello.]{voice="narrator"}'
    )
    project = _project(tmp_path, text)
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="narrator", voice="af_heart"
        ),
    )

    narrator = _roles(project)["narrator"]

    assert narrator.effective_voice == "af_bella"
    assert narrator.origin == "document"
    assert narrator.document_binding == "af_bella"
    assert narrator.project_binding == "af_heart"
    assert narrator.locations[0].lines == (7,)


def test_multiscope_document_bindings_report_mixed_values(tmp_path) -> None:
    project = _project(tmp_path, "Unused source.")
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
    bodies = (
        (
            "---\nssmd_version: '0.9'\nvoice_bindings:\n  kokoro:\n    narrator: af_sarah\n---\n"
            '[One.]{voice="narrator"}'
        ),
        (
            "---\nssmd_version: '0.9'\nvoice_bindings:\n  kokoro:\n    narrator: am_michael\n---\n"
            '[Two.]{voice="narrator"}'
        ),
    )
    for scope, body in zip(scopes, bodies):
        path = project.path(scope.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    project.paths["document_index"].write_text(
        json.dumps(DocumentIndex(scopes=scopes).to_dict()), encoding="utf-8"
    )
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="narrator", voice="af_heart"
        ),
    )

    narrator = _roles(project)["narrator"]

    assert narrator.uses == 2
    assert narrator.scope_count == 2
    assert narrator.status == "mixed"
    assert narrator.origin == "mixed"
    assert narrator.effective_voice is None
    assert narrator.document_binding is None
    assert narrator.document_bindings == {
        "chapter-0001": "af_sarah",
        "chapter-0002": "am_michael",
    }
    assert narrator.effective_by_scope["chapter-0001"]["origin"] == "document"
    assert narrator.effective_by_scope["chapter-0002"]["voice"] == "am_michael"
    assert narrator.locations[0].lines == (7,)


def test_direct_and_unresolved_references_are_reported_without_model_loading(tmp_path) -> None:
    project = _project(
        tmp_path,
        '[Direct.]{voice="am_michael"} [Unresolved.]{voice="not-configured"}',
    )

    roles = _roles(project)

    assert roles["am_michael"].effective_voice == "am_michael"
    assert roles["am_michael"].origin == "direct"
    assert roles["not-configured"].effective_voice is None
    assert roles["not-configured"].origin == "unresolved"


def test_bind_semantic_reference_persists_structured_target_without_editing_sources(
    tmp_path, monkeypatch
) -> None:
    project = _project(tmp_path, '[Hello.]{voice="narrator"}')
    cfg = ReadioConfig()
    source_before = project.paths["source"].read_bytes()
    document_before = project.paths["document_text"].read_bytes()
    config_roles_before = dict(cfg.voices["kokoro"].roles)

    monkeypatch.setattr(
        "readio.project_roles.resolve_voice_reference",
        lambda voice, **kwargs: SimpleNamespace(
            requested=voice,
            ref="kokoro:v1.0/af_heart",
            engine="pykokoro",
            voice="af_heart",
            target_id="v1.0",
        ),
    )

    result = bind_project_role(project, cfg, "narrator", "kokoro:v1.0/af_heart")

    assert result["provider"] == "kokoro"
    assert result["requested_voice"] == "kokoro:v1.0/af_heart"
    assert result["stored_voice"] == "af_heart"
    assert result["target"] == {"engine": "pykokoro", "voice": "af_heart", "target_id": "v1.0"}
    assert result["effective_voice"] == "af_heart"
    assert result["origin"] == "project"
    updated = load_project(project.root)
    assert project_role_bindings(updated.manifest)["narrator"] == VoiceTarget(
        "pykokoro", "af_heart", target_id="v1.0"
    )
    assert updated.paths["source"].read_bytes() == source_before
    assert updated.paths["document_text"].read_bytes() == document_before
    assert dict(cfg.voices["kokoro"].roles) == config_roles_before


def test_bind_rejects_unknown_and_document_bound_roles(tmp_path) -> None:
    project = _project(tmp_path, '[Hello.]{voice="narrator"}')
    cfg = ReadioConfig()

    with pytest.raises(ProjectRoleError) as unknown:
        bind_project_role(project, cfg, "narator", "af_heart")
    assert unknown.value.code == "readio.project_role.unknown"
    assert unknown.value.details["available_roles"] == ["narrator"]

    bound_project = _project(
        tmp_path / "bound",
        "---\nssmd_version: '0.9'\nvoice_bindings:\n  kokoro:\n    narrator: af_sarah\n---\n"
        '[Hello.]{voice="narrator"}',
    )
    with pytest.raises(ProjectRoleError) as document_bound:
        bind_project_role(bound_project, cfg, "narrator", "af_heart")
    assert document_bound.value.code == "readio.project_role.document_bound"
    assert document_bound.value.details["document_voice"] == "af_sarah"
    assert document_bound.value.details["scopes"] == ["document"]


def test_bind_rejects_semantic_reference_provider_mismatch(tmp_path, monkeypatch) -> None:
    project = _project(tmp_path, '[Hello.]{voice="narrator"}')
    monkeypatch.setattr(
        "readio.project_roles.resolve_voice_reference",
        lambda voice, **kwargs: SimpleNamespace(
            ref="kokoro:v1.0/af_heart",
            engine="pykokoro",
            voice="af_heart",
            target_id="v1.0",
        ),
    )

    with pytest.raises(ProjectRoleError) as mismatch:
        bind_project_role(
            project, ReadioConfig(), "narrator", "kokoro:v1.0/af_heart", provider="piper"
        )
    assert mismatch.value.code == "readio.project_role.provider_mismatch"


def test_unbind_removes_only_project_override_and_reports_config_fallback(tmp_path) -> None:
    project = _project(tmp_path, '[Hello.]{voice="narrator"}')
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            with_project_voice_binding(
                manifest, provider="kokoro", role="narrator", voice="af_heart"
            ),
            provider="piper",
            role="guest",
            voice="en_US-lessac-medium",
        ),
    )

    result = unbind_project_role(project, ReadioConfig(), "narrator", provider="kokoro")

    assert result["removed_voice"] == "af_heart"
    assert result["effective_voice"] == "af_sarah"
    assert result["origin"] == "config.voice_role"
    bindings = project_voice_bindings(load_project(project.root).manifest, "kokoro")
    assert "narrator" not in bindings
    assert project_voice_bindings(load_project(project.root).manifest, "piper") == {
        "guest": "en_US-lessac-medium"
    }


def test_unbind_without_project_override_fails_clearly(tmp_path) -> None:
    project = _project(tmp_path, '[Hello.]{voice="narrator"}')

    with pytest.raises(ProjectRoleError) as missing:
        unbind_project_role(project, ReadioConfig(), "narrator")

    assert missing.value.code == "readio.project_role.binding_missing"


def test_unique_project_binding_provider_is_inferred(tmp_path) -> None:
    project = _project(tmp_path, '[Hello.]{voice="guest"}')
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="piper", role="guest", voice="en_US-amy-medium"
        ),
    )

    assert resolve_project_voice_provider(project.manifest, ReadioConfig()) == "piper"

    inspection = inspect_project_roles(project, ReadioConfig())
    assert inspection.provider == "piper"
    assert inspection.roles[0].effective_voice == "en_US-amy-medium"


def test_project_voice_provider_precedence(tmp_path) -> None:
    project = _project(tmp_path, '[Hello.]{voice="guest"}')
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_provider(
            with_project_voice_binding(
                manifest, provider="piper", role="guest", voice="en_US-amy-medium"
            ),
            "piper",
        ),
    )

    assert (
        resolve_project_voice_provider(project.manifest, ReadioConfig(), explicit_provider="kokoro")
        == "kokoro"
    )
    assert (
        resolve_project_voice_provider(project.manifest, ReadioConfig(), explicit_engine="pykokoro")
        == "kokoro"
    )
    assert resolve_project_voice_provider(project.manifest, ReadioConfig()) == "piper"


def test_multiple_project_binding_providers_without_active_provider_are_ambiguous(
    tmp_path,
) -> None:
    project = _project(tmp_path, '[Hello.]{voice="guest"}')
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            with_project_voice_binding(manifest, provider="kokoro", role="guest", voice="af_bella"),
            provider="piper",
            role="guest",
            voice="en_US-amy-medium",
        ),
    )

    with pytest.raises(ProjectVoiceProviderError) as ambiguity:
        resolve_project_voice_provider(project.manifest, ReadioConfig())

    assert ambiguity.value.code == "readio.project_voice_provider_ambiguous"
    assert ambiguity.value.details == {"providers": ["kokoro", "piper"]}


def test_empty_binding_namespace_is_not_inferred(tmp_path) -> None:
    project = _project(tmp_path, '[Hello.]{voice="guest"}')
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="guest", voice="af_bella"
        ),
    )
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="piper", role="temp", voice="en_US-amy-medium"
        ),
    )
    project = update_project_manifest(
        project,
        lambda manifest: without_project_voice_binding(manifest, provider="piper", role="temp"),
    )

    assert project_voice_binding_providers(project.manifest) == ("kokoro",)
    assert resolve_project_voice_provider(project.manifest, ReadioConfig()) == "kokoro"


def test_piper_semantic_reference_binding_persists_structured_target(tmp_path, monkeypatch) -> None:
    project = _project(tmp_path, '[Hello.]{voice="guest"}')
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="guest", voice="af_bella"
        ),
    )
    monkeypatch.setattr(
        "readio.project_roles.resolve_voice_reference",
        lambda voice, **kwargs: SimpleNamespace(
            requested=voice,
            ref="piper:en_US-amy-medium",
            engine="piper",
            voice="en_US-amy-medium",
            target_id="en_US-amy-medium",
        ),
    )
    monkeypatch.setattr(
        "readio.engines.registry.get_engine",
        lambda engine: SimpleNamespace(
            capabilities=lambda: SimpleNamespace(voice_binding_namespace="piper")
        ),
    )

    result = bind_project_role(project, ReadioConfig(), "guest", "piper:en_US-amy-medium")
    updated = load_project(project.root)
    ssmd = updated.manifest.settings["ssmd"]

    assert result["provider"] == "piper"
    assert result["engine"] == "piper"
    assert result["stored_voice"] == "en_US-amy-medium"
    expected_target = {
        "engine": "piper",
        "voice": "en_US-amy-medium",
        "target_id": "en_US-amy-medium",
    }
    assert result["target"] == expected_target
    assert "voice_provider" not in ssmd
    assert ssmd["voice_bindings"] == {"kokoro": {"guest": "af_bella"}}
    assert ssmd["role_bindings"] == {"guest": expected_target}


def test_project_roles_explicit_provider_filters_effective_targets(tmp_path) -> None:
    project = _project(tmp_path, '[Hello.]{voice="guest"}')
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_provider(
            with_project_voice_binding(
                manifest, provider="piper", role="guest", voice="en_US-amy-medium"
            ),
            "piper",
        ),
    )

    inspection = inspect_project_roles(project, ReadioConfig(), provider="kokoro")

    assert inspection.provider == "kokoro"
    assert inspection.roles == ()


def test_unbind_defaults_to_active_provider_and_removes_empty_namespace(tmp_path) -> None:
    project = _project(tmp_path, '[Hello.]{voice="guest"}')
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_provider(
            with_project_voice_binding(
                with_project_voice_binding(
                    manifest, provider="kokoro", role="guest", voice="af_heart"
                ),
                provider="piper",
                role="guest",
                voice="en_US-amy-medium",
            ),
            "piper",
        ),
    )

    result = unbind_project_role(project, ReadioConfig(), "guest")
    ssmd = load_project(project.root).manifest.settings["ssmd"]

    assert result["provider"] == "piper"
    assert ssmd["voice_provider"] == "piper"
    assert ssmd["voice_bindings"] == {"kokoro": {"guest": "af_heart"}}


def test_bind_checks_document_binding_for_semantic_target(tmp_path, monkeypatch) -> None:
    text = (
        "---\nssmd_version: '0.9'\nvoice_bindings:\n  piper:\n    guest: en_US-bryce-medium\n---\n"
        '[Hello.]{voice="guest"}'
    )
    project = _project(tmp_path, text)
    monkeypatch.setattr(
        "readio.project_roles.resolve_voice_reference",
        lambda voice, **kwargs: SimpleNamespace(
            ref="piper:en_US-amy-medium",
            engine="piper",
            voice="en_US-amy-medium",
            target_id="en_US-amy-medium",
        ),
    )

    with pytest.raises(ProjectRoleError) as document_bound:
        bind_project_role(project, ReadioConfig(), "guest", "piper:en_US-amy-medium")

    assert document_bound.value.code == "readio.project_role.document_bound"
    assert document_bound.value.details["provider"] == "piper"


def test_role_centric_project_binding_preserves_legacy_settings(tmp_path) -> None:
    project = _project(tmp_path, '[Hello.]{voice="guest"}')
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="host", voice="af_sarah"
        ),
    )
    target = VoiceTarget(
        engine="pipersynth",
        voice="en_US-amy-medium",
        target_id="amy-asset",
    )

    project = update_project_manifest(
        project,
        lambda manifest: with_project_role_binding(manifest, role="guest", target=target),
    )

    assert project_role_bindings(project.manifest)["guest"] == VoiceTarget(
        engine="piper",
        voice="en_US-amy-medium",
        target_id="amy-asset",
    )
    settings = project.manifest.settings["ssmd"]
    assert settings["role_bindings"]["guest"]["engine"] == "piper"
    assert settings["role_bindings"]["guest"] == {
        "engine": "piper",
        "voice": "en_US-amy-medium",
        "target_id": "amy-asset",
    }
    assert "selector" not in settings["role_bindings"]["guest"]
    assert settings["voice_bindings"] == {"kokoro": {"host": "af_sarah"}}

    assert "voice_provider" not in settings

    project = update_project_manifest(
        project,
        lambda manifest: without_project_role_binding(manifest, role="guest"),
    )
    assert project_role_bindings(project.manifest) == {}
    assert project.manifest.settings["ssmd"]["voice_bindings"] == {"kokoro": {"host": "af_sarah"}}


def test_inspection_resolves_mixed_project_targets_without_project_provider_selection(
    tmp_path,
) -> None:
    project = _project(
        tmp_path,
        '[Hello.]{voice="host"}\n\n[Question.]{voice="guest"}',
    )
    project = update_project_manifest(
        project,
        lambda manifest: with_project_role_binding(
            with_project_voice_binding(manifest, provider="kokoro", role="host", voice="af_sarah"),
            role="guest",
            target=VoiceTarget("piper", "en_US-amy-medium"),
        ),
    )

    inspection = inspect_project_roles(project, ReadioConfig())
    roles = {role.role: role for role in inspection.roles}

    assert inspection.provider is None
    assert roles["host"].effective_target == VoiceTarget("pykokoro", "af_sarah")
    assert roles["guest"].effective_target == VoiceTarget("piper", "en_US-amy-medium")
