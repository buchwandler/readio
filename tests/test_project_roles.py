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
    project_voice_binding_providers,
    project_voice_bindings,
    project_voice_bindings_provenance,
    resolve_project_voice_provider,
    with_project_voice_binding,
    with_project_voice_provider,
    without_project_voice_binding,
)


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
        f'<div voice="{role}">Line {index}.</div>'
        for role, count in expected_counts.items()
        for index in range(count)
    ]
    project = _project(tmp_path, "\n".join(lines))
    project.paths["source"].write_text(
        '<div voice="guest">Only current source.</div>', encoding="utf-8"
    )
    assert not project.paths["plan_index"].exists()

    roles = _roles(project)

    assert {name: role.uses for name, role in roles.items()} == {"guest": 1}
    assert roles["guest"].locations[0].scope_id == "document"
    assert roles["guest"].locations[0].lines == (1,)
    assert roles["guest"].origin == "config.voice_role"


def test_collects_all_sample_role_counts_and_config_fallback(tmp_path) -> None:
    expected_counts = {"narrator": 9, "host": 9, "guest": 11}
    lines = [
        f'<div voice="{role}">Line {index}.</div>'
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
    project = _project(tmp_path, '<div voice="narrator">Hello.</div>')
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
        "---\nvoice_bindings:\n  kokoro:\n    narrator: af_bella\n---\n"
        '<div voice="narrator">Hello.</div>'
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
    assert narrator.locations[0].lines == (6,)


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
            "---\nvoice_bindings:\n  kokoro:\n    narrator: af_sarah\n---\n"
            '<div voice="narrator">One.</div>'
        ),
        (
            "---\nvoice_bindings:\n  kokoro:\n    narrator: am_michael\n---\n"
            '<div voice="narrator">Two.</div>'
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
    assert narrator.locations[0].lines == (6,)


def test_direct_and_unresolved_references_are_reported_without_model_loading(tmp_path) -> None:
    project = _project(
        tmp_path,
        '<div voice="am_michael">Direct.</div>\n'
        '<div voice="not-configured">Unresolved.</div>',
    )

    roles = _roles(project)

    assert roles["am_michael"].effective_voice == "am_michael"
    assert roles["am_michael"].origin == "direct"
    assert roles["not-configured"].effective_voice is None
    assert roles["not-configured"].origin == "unresolved"


def test_bind_stable_selector_persists_canonical_voice_without_editing_sources(
    tmp_path, monkeypatch
 ) -> None:
    project = _project(tmp_path, '<div voice="narrator">Hello.</div>')
    cfg = ReadioConfig()
    source_before = project.paths["source"].read_bytes()
    document_before = project.paths["document_text"].read_bytes()
    config_roles_before = dict(cfg.voices["kokoro"].roles)

    monkeypatch.setattr(
        "readio.project_roles.resolve_voice_selector",
        lambda voice, **kwargs: SimpleNamespace(
            requested=voice,
            selector="en_us-ko-4",
            engine="pykokoro",
            voice="af_heart",
        ),
    )

    result = bind_project_role(project, cfg, "narrator", "en_us-ko-4")

    assert result["provider"] == "kokoro"
    assert result["requested_voice"] == "en_us-ko-4"
    assert result["stored_voice"] == "af_heart"
    assert result["effective_voice"] == "af_heart"
    assert result["origin"] == "project"
    updated = load_project(project.root)
    assert project_voice_bindings(updated.manifest, "kokoro") == {
        "narrator": "af_heart"
    }
    assert updated.paths["source"].read_bytes() == source_before
    assert updated.paths["document_text"].read_bytes() == document_before
    assert dict(cfg.voices["kokoro"].roles) == config_roles_before


def test_bind_rejects_unknown_and_document_bound_roles(tmp_path) -> None:
    project = _project(tmp_path, '<div voice="narrator">Hello.</div>')
    cfg = ReadioConfig()

    with pytest.raises(ProjectRoleError) as unknown:
        bind_project_role(project, cfg, "narator", "af_heart")
    assert unknown.value.code == "readio.project_role.unknown"
    assert unknown.value.details["available_roles"] == ["narrator"]

    bound_project = _project(
        tmp_path / "bound",
        "---\nvoice_bindings:\n  kokoro:\n    narrator: af_sarah\n---\n"
        '<div voice="narrator">Hello.</div>',
    )
    with pytest.raises(ProjectRoleError) as document_bound:
        bind_project_role(bound_project, cfg, "narrator", "af_heart")
    assert document_bound.value.code == "readio.project_role.document_bound"
    assert document_bound.value.details["document_voice"] == "af_sarah"
    assert document_bound.value.details["scopes"] == ["document"]


def test_bind_rejects_selector_provider_mismatch(tmp_path, monkeypatch) -> None:
    project = _project(tmp_path, '<div voice="narrator">Hello.</div>')
    monkeypatch.setattr(
        "readio.project_roles.resolve_voice_selector",
        lambda voice, **kwargs: SimpleNamespace(
            selector="en_us-ko-4", engine="pykokoro", voice="af_heart"
        ),
    )

    with pytest.raises(ProjectRoleError) as mismatch:
        bind_project_role(
            project, ReadioConfig(), "narrator", "en_us-ko-4", provider="piper"
        )
    assert mismatch.value.code == "readio.project_role.provider_mismatch"


def test_unbind_removes_only_project_override_and_reports_config_fallback(tmp_path) -> None:
    project = _project(tmp_path, '<div voice="narrator">Hello.</div>')
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
    project = _project(tmp_path, '<div voice="narrator">Hello.</div>')

    with pytest.raises(ProjectRoleError) as missing:
        unbind_project_role(project, ReadioConfig(), "narrator")

    assert missing.value.code == "readio.project_role.binding_missing"



def test_unique_project_binding_provider_is_inferred(tmp_path) -> None:
    project = _project(tmp_path, '<div voice="guest">Hello.</div>')
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
    project = _project(tmp_path, '<div voice="guest">Hello.</div>')
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_provider(
            with_project_voice_binding(
                manifest, provider="piper", role="guest", voice="en_US-amy-medium"
            ),
            "piper",
        ),
    )

    assert resolve_project_voice_provider(
        project.manifest, ReadioConfig(), explicit_provider="kokoro"
    ) == "kokoro"
    assert resolve_project_voice_provider(
        project.manifest, ReadioConfig(), explicit_engine="pykokoro"
    ) == "kokoro"
    assert resolve_project_voice_provider(project.manifest, ReadioConfig()) == "piper"


def test_multiple_project_binding_providers_without_active_provider_are_ambiguous(
    tmp_path,
) -> None:
    project = _project(tmp_path, '<div voice="guest">Hello.</div>')
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            with_project_voice_binding(
                manifest, provider="kokoro", role="guest", voice="af_bella"
            ),
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
    project = _project(tmp_path, '<div voice="guest">Hello.</div>')
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
        lambda manifest: without_project_voice_binding(
            manifest, provider="piper", role="temp"
        ),
    )

    assert project_voice_binding_providers(project.manifest) == ("kokoro",)
    assert resolve_project_voice_provider(project.manifest, ReadioConfig()) == "kokoro"


def test_piper_selector_binding_sets_active_provider_and_preserves_kokoro(
    tmp_path, monkeypatch
 ) -> None:
    project = _project(tmp_path, '<div voice="guest">Hello.</div>')
    project = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="guest", voice="af_bella"
        ),
    )
    monkeypatch.setattr(
        "readio.project_roles.resolve_voice_selector",
        lambda voice, **kwargs: SimpleNamespace(
            requested=voice,
            selector="en-pi-13",
            engine="piper",
            voice="en_US-amy-medium",
        ),
    )
    monkeypatch.setattr(
        "readio.engines.registry.get_engine",
        lambda engine: SimpleNamespace(
            capabilities=lambda: SimpleNamespace(ssmd_provider="piper")
        ),
    )

    result = bind_project_role(project, ReadioConfig(), "guest", "en-pi-13")
    updated = load_project(project.root)
    ssmd = updated.manifest.settings["ssmd"]

    assert result["provider"] == "piper"
    assert result["stored_voice"] == "en_US-amy-medium"
    assert ssmd["voice_provider"] == "piper"
    assert ssmd["voice_bindings"] == {
        "kokoro": {"guest": "af_bella"},
        "piper": {"guest": "en_US-amy-medium"},
    }


def test_project_roles_explicit_provider_overrides_active_provider(tmp_path) -> None:
    project = _project(tmp_path, '<div voice="guest">Hello.</div>')
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
    assert inspection.roles[0].effective_voice == "af_bella"


def test_unbind_defaults_to_active_provider_and_removes_empty_namespace(tmp_path) -> None:
    project = _project(tmp_path, '<div voice="guest">Hello.</div>')
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


def test_bind_checks_document_binding_in_selector_provider(tmp_path, monkeypatch) -> None:
    text = (
        "---\nvoice_bindings:\n  piper:\n    guest: en_US-bryce-medium\n---\n"
        '<div voice="guest">Hello.</div>'
    )
    project = _project(tmp_path, text)
    monkeypatch.setattr(
        "readio.project_roles.resolve_voice_selector",
        lambda voice, **kwargs: SimpleNamespace(
            requested=voice,
            selector="en-pi-13",
            engine="piper",
            voice="en_US-amy-medium",
        ),
    )
    monkeypatch.setattr(
        "readio.engines.registry.get_engine",
        lambda engine: SimpleNamespace(
            capabilities=lambda: SimpleNamespace(ssmd_provider="piper")
        ),
    )

    with pytest.raises(ProjectRoleError) as document_bound:
        bind_project_role(project, ReadioConfig(), "guest", "en-pi-13")

    assert document_bound.value.code == "readio.project_role.document_bound"
    assert document_bound.value.details["provider"] == "piper"
