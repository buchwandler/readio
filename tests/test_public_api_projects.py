from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from audiobook_support import make_epub
from project_support import Adapter

from readio.api import (
    AudiobookChapter,
    AudiobookInspection,
    AudiobookProjectDescription,
    AudiobookProjectResult,
    CompositionOptions,
    ExportOptions,
    InvalidRequestError,
    PreviewRequest,
    ProjectBuildRequest,
    ProjectBuildResult,
    ProjectConflictError,
    ProjectError,
    ProjectFormatError,
    ProjectNotFoundError,
    ProjectPlanResult,
    ProjectRef,
    ProjectStatus,
    ProjectSynthesisResult,
    Readio,
    SynthesisResolution,
)
from readio.config import LanguageSettings, ReaderSettings, ReadioConfig, VoiceProviderSettings
from readio.engines.registry import _registry
from readio.plan import SynthesisRequest


def _project_snapshot(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_project_lifecycle_planning_and_typed_status(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("A project API plan.", encoding="utf-8")
    root = tmp_path / "source.readio"
    app = Readio(ReadioConfig())

    project = app.projects.create(source, output=root)

    assert isinstance(project, ProjectRef)
    assert app.projects.find(root) == project
    assert app.projects.open(root) == project
    with patch(
        "readio.engines.pykokoro.PyKokoroEngineAdapter.open",
        side_effect=AssertionError("synthesis runtime opened"),
    ):
        result = app.projects.plan(project)

    assert isinstance(result, ProjectPlanResult)
    assert result.scopes
    assert json.loads(json.dumps(result.to_dict()))["project"]["project_id"] == project.project_id
    status = app.projects.status(project)
    assert isinstance(status, ProjectStatus)
    assert status.stage("plan").state == "current"
    assert json.loads(json.dumps(status.to_dict()))["stages"]


def test_project_compose_returns_typed_mastering_diagnostics(tmp_path: Path, monkeypatch) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice")))
    source = tmp_path / "spoken.txt"
    source.write_text("A short spoken-word passage.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "spoken.readio")
    app.projects.plan(project)
    app.projects.synthesize(project, SynthesisRequest(engine="fake", voice="fake-voice"))

    result = app.projects.compose(project)

    assert result.loudness is not None
    assert result.loudness.profile == "spoken-word"
    assert result.loudness.target_lufs == -16.0
    assert result.loudness.true_peak_ceiling_dbtp == -1.0
    assert result.loudness.integrated_lufs_before is None
    assert result.loudness.sample_peak_dbfs_before is not None
    assert result.loudness.true_peak_dbtp_before is not None
    assert result.loudness.warning is not None
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["loudness"]["profile"] == "spoken-word"
    state_path = tmp_path / "spoken.readio" / "composition" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    policy = state["identity_payload"]["loudness"]
    assert policy["profile"] == "spoken-word"
    assert policy["target_lufs"] == -16.0
    assert "analysis_seconds" not in policy
    assert state["loudness"]["analysis_seconds"] >= 0.0
    assert state["loudness"]["applied_gain_db"] == result.loudness.applied_gain_db


def test_project_build_returns_typed_incremental_operations(tmp_path: Path, monkeypatch) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice")))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "book.readio")
    request = ProjectBuildRequest(
        synthesis=SynthesisRequest(engine="fake", voice="fake-voice"),
        export=ExportOptions(format="wav"),
    )

    first = app.projects.build(project, request)
    second = app.projects.build(project, request)

    assert isinstance(first, ProjectBuildResult)
    assert first.output_path is not None and first.output_path.is_file()
    assert [operation.action for operation in second.operations] == [
        "skipped",
        "skipped",
        "skipped",
        "skipped",
    ]
    assert adapter.open_calls == 1
    assert json.loads(json.dumps(first.to_dict()))["operations"]

    composition_only = app.projects.build(
        project,
        ProjectBuildRequest(
            synthesis=request.synthesis,
            composition=CompositionOptions(target_lufs=-18),
            export=request.export,
        ),
    )
    assert composition_only.operations[1].action == "skipped"
    assert composition_only.operations[2].action == "rebuilt"
    assert adapter.open_calls == 1


def test_project_synthesis_target_stops_before_composition(tmp_path: Path, monkeypatch) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice")))
    source = tmp_path / "book.txt"
    source.write_text("Only synthesize.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "book.readio")

    result = app.projects.build(
        project,
        ProjectBuildRequest(
            target="synthesis",
            synthesis=SynthesisRequest(engine="fake", voice="fake-voice"),
        ),
    )

    assert [operation.stage for operation in result.operations] == ["plan", "synthesis"]
    assert not any(operation.stage == "composition" for operation in result.operations)
    direct = app.projects.synthesize(
        project,
        SynthesisRequest(engine="fake", voice="fake-voice"),
    )
    assert isinstance(direct, ProjectSynthesisResult)


def test_audiobook_inspection_and_project_creation_are_typed(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    make_epub(source)
    app = Readio(ReadioConfig())

    inspection = app.audiobooks.inspect(source)
    project = app.audiobooks.create_project(
        source,
        chapters="1-2",
        output=tmp_path / "book.readio",
    )

    assert isinstance(inspection, AudiobookInspection)
    assert all(isinstance(chapter, AudiobookChapter) for chapter in inspection.chapters)
    assert len(inspection.chapters) == 7
    assert json.loads(json.dumps(inspection.to_dict()))["metadata"]["title"] == "The Example"
    assert project.kind == "audiobook"
    assert app.projects.open(project.root) == project
    assert app.projects.status(project).stage("document").state == "current"
    creation = app.audiobooks.create_project_result(
        source, chapters="2-3", output=tmp_path / "selected.readio"
    )
    assert isinstance(creation, AudiobookProjectResult)
    assert creation.selected_chapters == 2
    assert [chapter.number for chapter in creation.chapters] == [2, 3]


def test_describe_reopened_audiobook_project_returns_persisted_chapters(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    make_epub(source)
    app = Readio(ReadioConfig())
    created = app.audiobooks.create_project_result(
        source, chapters="2-3", output=tmp_path / "book.readio"
    )
    reopened = app.projects.open(created.project.root)

    description = app.audiobooks.describe_project(reopened)

    assert isinstance(description, AudiobookProjectDescription)
    assert description.project == created.project
    assert description.source == created.project.root / "source" / "book.epub"
    assert description.chapters == created.chapters
    payload = json.loads(json.dumps(description.to_dict()))
    assert [chapter["number"] for chapter in payload["chapters"]] == [2, 3]
    assert [chapter["scope_id"] for chapter in payload["chapters"]] == [
        "chapter-0002",
        "chapter-0003",
    ]
    assert all(chapter["title"] and chapter["level"] for chapter in payload["chapters"])


def test_describe_project_rejects_non_audiobook_and_translates_load_errors(
    tmp_path: Path,
) -> None:
    app = Readio(ReadioConfig())
    source = tmp_path / "document.txt"
    source.write_text("Not an audiobook.", encoding="utf-8")
    document_project = app.projects.create(source, output=tmp_path / "document.readio")
    with pytest.raises(InvalidRequestError) as wrong_kind:
        app.audiobooks.describe_project(document_project)
    assert wrong_kind.value.code == "audiobook.project_kind_invalid"

    with pytest.raises(ProjectNotFoundError) as missing:
        app.audiobooks.describe_project(tmp_path / "missing.readio")
    assert missing.value.code == "project.not_found"

    epub = tmp_path / "book.epub"
    make_epub(epub)
    audiobook = app.audiobooks.create_project(epub, output=tmp_path / "book.readio")
    (audiobook.root / "document" / "index.json").write_text("{", encoding="utf-8")
    with pytest.raises(ProjectFormatError) as corrupt:
        app.audiobooks.describe_project(audiobook)
    assert corrupt.value.code == "project.invalid"


def test_resolve_synthesis_uses_profile_and_reader_settings_without_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    config = ReadioConfig(
        reader=ReaderSettings(
            engine="fake",
            voice="reader-voice",
            lang="en-us",
            speed=1.25,
            unit="sentence",
            pause_mode="manual",
            voice_level="calibrated",
        ),
        languages={
            "en-us": LanguageSettings(
                engine="fake",
                model="profile-model",
                source="profile-source",
                quality="profile-quality",
                voice="profile-voice",
            )
        },
    )
    app = Readio(config)
    source = tmp_path / "preflight.txt"
    source.write_text("Preflight only.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "preflight.readio")
    before = _project_snapshot(project.root)

    resolution = app.projects.resolve_synthesis(project)

    assert isinstance(resolution, SynthesisResolution)
    assert resolution.engine == "fake"
    assert resolution.language == "en-us"
    assert resolution.voice == "reader-voice"
    assert resolution.model == "profile-model"
    assert resolution.model_source == "profile-source"
    assert resolution.quality == "profile-quality"
    assert resolution.speed == 1.25
    assert resolution.unit == "sentence"
    assert resolution.pause_mode == "manual"
    assert resolution.voice_level == "calibrated"
    assert resolution.provider == "fake"
    assert resolution.lexicons is None
    assert adapter.open_calls == 0
    assert _project_snapshot(project.root) == before
    assert json.loads(json.dumps(resolution.to_dict()))["model"] == "profile-model"


def test_resolve_synthesis_exposes_pronunciation_options_and_empty_lexicons(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice")))
    source = tmp_path / "pronunciation.txt"
    source.write_text("Preflight pronunciation settings.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "pronunciation.readio")
    before = _project_snapshot(project.root)

    request = SynthesisRequest(
        engine="fake",
        voice="fake-voice",
        lexicons=("lex-a", "lex-b"),
        g2p_fallback="espeak",
        lexicon_data_policy="installed-only",
        allow_experimental=True,
        language_detection="auto",
        detect_languages=("en-us", "de"),
    )
    resolution = app.projects.resolve_synthesis(project, request)

    assert resolution.lexicons == ("lex-a", "lex-b")
    assert resolution.g2p_fallback == "espeak"
    assert resolution.lexicon_data_policy == "installed-only"
    assert resolution.allow_experimental is True
    assert resolution.language_detection == "auto"
    assert resolution.detect_languages == ("en-us", "de")
    assert isinstance(resolution.lexicons, tuple)
    assert isinstance(resolution.detect_languages, tuple)
    assert adapter.open_calls == 0
    assert _project_snapshot(project.root) == before

    no_lexicons = app.projects.resolve_synthesis(
        project, SynthesisRequest(engine="fake", voice="fake-voice", lexicons=())
    )
    assert no_lexicons.lexicons == ()
    assert adapter.open_calls == 0
    assert _project_snapshot(project.root) == before


def test_resolve_synthesis_respects_request_overrides_and_voice_bindings(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(
        ReadioConfig(
            reader=ReaderSettings(engine="fake", voice="reader-voice"),
            voices={"fake": VoiceProviderSettings(ids=("bound-voice", "project-voice"), roles={})},
        )
    )
    source = tmp_path / "voices.ssmd"
    source.write_text(
        "---\nssmd_version: '0.9'\n---\n[Bound voice.]{voice=\"narrator\"}",
        encoding="utf-8",
    )
    project = app.projects.create(source, output=tmp_path / "voices.readio")

    request = SynthesisRequest(
        engine="fake",
        language="fr-fr",
        model="explicit-model",
        model_source="explicit-source",
        quality="explicit-quality",
        speed=1.75,
        voice_level="off",
        unit="paragraph",
        pause_mode="tts",
        spacy="off",
        short_sentence="phrase",
    )
    resolution = app.projects.resolve_synthesis(
        project, request, voice_bindings={"narrator": "bound-voice"}
    )

    assert resolution.language == "fr-fr"
    assert resolution.voice == "bound-voice"
    assert resolution.model == "explicit-model"
    assert resolution.model_source == "explicit-source"
    assert resolution.quality == "explicit-quality"
    assert resolution.speed == 1.75
    assert resolution.voice_level == "off"
    assert resolution.unit == "paragraph"
    assert resolution.pause_mode == "tts"
    assert resolution.spacy == "off"
    assert resolution.short_sentence == "phrase"
    assert adapter.open_calls == 0
    app.roles.bind_project(project, "narrator", "project-voice", provider="fake")
    persisted = app.projects.resolve_synthesis(project, SynthesisRequest(engine="fake"))
    assert persisted.voice == "project-voice"
    assert adapter.open_calls == 0


def test_resolve_synthesis_matches_actual_profile_and_rejects_invalid_voice(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice")))
    source = tmp_path / "parity.txt"
    source.write_text("Execution parity.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "parity.readio")
    app.projects.plan(project)
    request = SynthesisRequest(engine="fake", voice="fake-voice", speed=1.4)
    before = _project_snapshot(project.root)

    resolution = app.projects.resolve_synthesis(project, request)

    assert _project_snapshot(project.root) == before
    assert adapter.open_calls == 0
    result = app.projects.synthesize(project, request)
    profile = json.loads((project.root / "synthesis" / "profile.json").read_text())
    canonical = profile["canonical"]

    assert result.profile_id == profile["profile_id"]
    assert resolution.engine == canonical["engine"]
    actual_selection = canonical.get("targets", {}).get(resolution.voice, canonical)
    assert resolution.model == actual_selection["target_id"]
    assert resolution.voice == actual_selection["voice"]
    assert adapter.open_calls == 1

    original_resolve = adapter.resolve

    def reject_bad_voice(engine_request):
        if engine_request.voice == "invalid-selector":
            raise ValueError("unknown voice selector 'invalid-selector'")
        return original_resolve(engine_request)

    monkeypatch.setattr(adapter, "resolve", reject_bad_voice)
    with pytest.raises(InvalidRequestError, match="unknown voice selector") as invalid:
        app.projects.resolve_synthesis(
            project, SynthesisRequest(engine="fake", voice="invalid-selector")
        )
    assert invalid.value.code == "request.invalid"


def test_project_errors_are_translated_to_public_types(tmp_path: Path) -> None:
    app = Readio(ReadioConfig())
    with pytest.raises(ProjectNotFoundError) as missing:
        app.projects.open(tmp_path / "missing.readio")
    assert missing.value.code == "project.not_found"

    source = tmp_path / "locked.txt"
    source.write_text("Locked project.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "locked.readio")
    (project.root / ".lock").write_text("other-process plan", encoding="utf-8")
    with pytest.raises(ProjectConflictError) as locked:
        app.projects.plan(project)
    assert locked.value.code == "project.locked"


def test_preview_is_typed_and_does_not_activate_synthesis(tmp_path: Path, monkeypatch) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice")))
    source = tmp_path / "preview.txt"
    source.write_text("A preview paragraph.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "preview.readio")
    app.projects.plan(project)
    profile_path = project.root / "synthesis" / "profile.json"
    before = profile_path.read_bytes() if profile_path.exists() else None

    result = app.projects.preview(
        project,
        PreviewRequest(
            selection="first:1",
            synthesis=SynthesisRequest(engine="fake", voice="fake-voice"),
        ),
    )

    assert result.activated is False
    assert result.frames > 0
    assert json.loads(json.dumps(result.to_dict()))["items"] >= 0
    assert result.loudness is not None
    assert result.loudness.profile == "spoken-word"
    assert json.loads(json.dumps(result.to_dict()))["loudness"]["profile"] == "spoken-word"
    after = profile_path.read_bytes() if profile_path.exists() else None
    assert after == before


def test_status_preserves_missing_output_command_in_public_api(tmp_path: Path, monkeypatch) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice")))
    source = tmp_path / "status.txt"
    source.write_text("Current composition.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "status.readio")

    app.projects.plan(project)
    app.projects.synthesize(project, SynthesisRequest(engine="fake", voice="fake-voice"))
    app.projects.compose(project)

    status = app.projects.status(project)
    action = status.next_actions[0]
    assert action.stage == "output"
    assert action.reason == "output.missing"
    assert action.command == "readio export --format mp3"
    payload = json.loads(json.dumps(status.to_dict()))
    assert payload["next_actions"][0]["command"] == "readio export --format mp3"


def test_project_settings_configure_materializes_without_opening_engine(
    tmp_path: Path, monkeypatch
) -> None:
    from readio.api.types import (
        ProjectSettings,
        ProjectSynthesisSettings,
        ProjectSettingsPatch,
        UNSET,
    )

    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice")))
    source = tmp_path / "settings.txt"
    source.write_text("Persist preferences before execution.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "settings.readio")
    manifest_path = project.root / "project.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["settings"] = {"ssmd": {"style": "bright"}, "custom": {"keep": True}}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    before = manifest_path.read_bytes()

    assert app.projects.settings(project) == ProjectSettings()
    assert manifest_path.read_bytes() == before

    configured = app.projects.configure(
        project,
        ProjectSettings(
            synthesis=ProjectSynthesisSettings(
                engine="fake",
                voice="fake-voice",
                speed=1.25,
            ),
            composition=CompositionOptions(),
            export=ExportOptions(format="wav"),
        ),
    )

    assert configured.synthesis is not None
    assert configured.synthesis.language == "en-us"
    assert configured.synthesis.engine == "fake"
    assert configured.synthesis.model == "fake-target"
    assert configured.synthesis.speed == 1.25
    assert configured.synthesis.auto_lexicons is True
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))["settings"]
    assert persisted["ssmd"] == {"style": "bright"}
    assert persisted["custom"] == {"keep": True}
    assert app.projects.open(project.root) == project
    assert app.projects.settings(project) == configured
    assert adapter.open_calls == 0

    updated = app.projects.update_settings(
        project, ProjectSettingsPatch(synthesis=None, composition=None, export=UNSET)
    )
    assert updated.synthesis is None
    assert updated.composition is None
    assert updated.export == configured.export
    assert updated.audiobook_export is None
    assert adapter.open_calls == 0


def test_project_settings_validation_is_atomic(tmp_path: Path, monkeypatch) -> None:
    from readio.api.types import ProjectSettings, ProjectSynthesisSettings

    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice")))
    source = tmp_path / "invalid-settings.txt"
    source.write_text("Do not persist invalid settings.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "invalid-settings.readio")
    manifest_path = project.root / "project.json"
    before = manifest_path.read_bytes()

    with pytest.raises(ProjectFormatError):
        app.projects.configure(
            project,
            ProjectSettings(synthesis=ProjectSynthesisSettings(engine="fake", spacy="invalid")),
        )
    assert manifest_path.read_bytes() == before

    original_resolve = adapter.resolve

    def reject_invalid_voice(request):
        if request.voice == "invalid-selector":
            raise ValueError("unknown voice selector")
        return original_resolve(request)

    monkeypatch.setattr(adapter, "resolve", reject_invalid_voice)
    with pytest.raises(InvalidRequestError, match="unknown voice selector"):
        app.projects.configure(
            project,
            ProjectSettings(
                synthesis=ProjectSynthesisSettings(
                    engine="fake",
                    voice="invalid-selector",
                ),
            ),
        )
    assert manifest_path.read_bytes() == before
    assert adapter.open_calls == 0


def test_project_settings_configuration_observes_manifest_lock(tmp_path: Path) -> None:
    from readio.api.types import ProjectSettings

    app = Readio(ReadioConfig())
    source = tmp_path / "locked-settings.txt"
    source.write_text("Respect the project lock.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "locked-settings.readio")
    manifest_path = project.root / "project.json"
    before = manifest_path.read_bytes()
    (project.root / ".lock").write_text("other-process configure\n", encoding="utf-8")

    with pytest.raises(ProjectConflictError) as locked:
        app.projects.configure(project, ProjectSettings(export=ExportOptions()))

    assert locked.value.code == "project.locked"
    assert manifest_path.read_bytes() == before


def test_saved_synthesis_precedence_overrides_and_status(tmp_path: Path, monkeypatch) -> None:
    from dataclasses import replace
    from readio.api.types import ProjectSettingsPatch
    from readio.api.types import ProjectSettings, ProjectSynthesisSettings

    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="global-voice")))
    source = tmp_path / "saved-synthesis.txt"
    source.write_text("Use durable preferences.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "saved-synthesis.readio")
    manifest_path = project.root / "project.json"
    configured = app.projects.configure(
        project,
        ProjectSettings(
            synthesis=ProjectSynthesisSettings(
                engine="fake",
                language="fr-fr",
                model="saved-model",
                voice="saved-voice",
                speed=1.2,
            )
        ),
    )
    assert configured.synthesis is not None
    before_override = manifest_path.read_bytes()

    saved = app.projects.resolve_synthesis(project)
    assert saved.language == "fr-fr"
    assert saved.model == "saved-model"
    assert saved.voice == "saved-voice"
    assert saved.speed == 1.2
    overridden = app.projects.resolve_synthesis(project, SynthesisRequest(speed=1.75, refresh=True))
    assert overridden.language == "fr-fr"
    assert overridden.speed == 1.75
    assert manifest_path.read_bytes() == before_override
    assert app.projects.settings(project) == configured
    assert adapter.open_calls == 0

    app.projects.plan(project)
    result = app.projects.synthesize(project, SynthesisRequest(speed=1.75, refresh=True))
    assert result.rendered > 0
    override_profile = json.loads(
        (project.root / "synthesis" / "profile.json").read_text(encoding="utf-8")
    )
    reused = app.projects.synthesize(project, SynthesisRequest(speed=1.75, refresh=False))
    assert reused.rendered == 0
    profile_after_refresh_change = json.loads(
        (project.root / "synthesis" / "profile.json").read_text(encoding="utf-8")
    )
    assert profile_after_refresh_change["profile_id"] == override_profile["profile_id"]
    stale = {row.stage: row for row in app.projects.status(project).stages}
    assert stale["synthesis"].reason == "synthesis.stale.project_settings_changed"
    assert manifest_path.read_bytes() == before_override
    assert app.projects.settings(project) == configured

    app.projects.synthesize(project)
    current = {row.stage: row for row in app.projects.status(project).stages}
    assert current["synthesis"].state == "current"
    assert manifest_path.read_bytes() == before_override

    cached_files = sorted((project.root / "synthesis" / "cache").glob("*.wav"))
    app.projects.update_settings(
        project,
        ProjectSettingsPatch(synthesis=replace(configured.synthesis, speed=1.4)),
    )
    changed = {row.stage: row for row in app.projects.status(project).stages}
    assert changed["plan"].state == "current"
    assert changed["synthesis"].reason == "synthesis.stale.project_settings_changed"
    assert sorted((project.root / "synthesis" / "cache").glob("*.wav")) == cached_files
    app.projects.update_settings(
        project,
        ProjectSettingsPatch(synthesis=replace(configured.synthesis, language="de-de")),
    )
    plan_stale = {row.stage: row for row in app.projects.status(project).stages}
    assert plan_stale["plan"].reason == "plan.stale.project_settings_changed"
    app.projects.plan(project)
    replanned = {row.stage: row for row in app.projects.status(project).stages}
    assert replanned["plan"].state == "current"
    assert replanned["synthesis"].reason == "synthesis.stale.project_settings_changed"


def test_project_settings_survive_failed_build_and_retry(tmp_path: Path, monkeypatch) -> None:
    from readio.api.types import ProjectSettings, ProjectSynthesisSettings

    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="global-voice")))
    source = tmp_path / "retry.txt"
    source.write_text("Retry with persisted choices.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "retry.readio")
    configured = app.projects.configure(
        project,
        ProjectSettings(
            synthesis=ProjectSynthesisSettings(
                engine="fake", voice="saved-voice", language="fr-fr", speed=1.3
            )
        ),
    )
    manifest_path = project.root / "project.json"
    persisted = manifest_path.read_bytes()
    original_open = adapter.open

    def fail_open(selection):
        raise RuntimeError("synthetic runtime failure")

    monkeypatch.setattr(adapter, "open", fail_open)
    with pytest.raises(ProjectError, match="synthetic runtime failure"):
        app.projects.build(project)
    assert (project.root / "plan" / "index.json").is_file()
    plan_data = json.loads(
        (project.root / "plan" / "document.utterplan.json").read_text(encoding="utf-8")
    )
    assert plan_data["config"]["language"] == "fr-fr"
    assert manifest_path.read_bytes() == persisted
    assert app.projects.settings(project) == configured

    monkeypatch.setattr(adapter, "open", original_open)
    result = app.projects.build(project)
    assert result.output_path is not None and result.output_path.is_file()
    assert manifest_path.read_bytes() == persisted
    assert app.projects.settings(project) == configured
    assert adapter.open_calls == 1


def test_requestless_build_and_stage_apis_use_saved_composition_and_export(
    tmp_path: Path, monkeypatch
) -> None:
    from readio.api.types import (
        ProjectSettings,
        ProjectSettingsPatch,
        ProjectSynthesisSettings,
    )

    class FakeSink:
        def __init__(self, path: Path, bitrate: str | None) -> None:
            self.path = path
            self.bitrate = bitrate

        def __enter__(self):
            return self

        def write(self, _audio, _sample_rate: int) -> None:
            self.path.write_bytes((self.bitrate or "").encode("ascii"))

        def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
            return None

    monkeypatch.setattr(
        "readio.stages.export.ensure_audio_format_available", lambda _format: None
    )
    monkeypatch.setattr(
        "readio.stages.export.create_audio_sink",
        lambda path, _format, *, bitrate=None: FakeSink(path, bitrate),
    )

    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice")))
    source = tmp_path / "desired-build.txt"
    source.write_text("Build from saved stage settings.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "desired-build.readio")
    output_path = project.root / "exports" / "desired.m4a"
    configured = app.projects.configure(
        project,
        ProjectSettings(
            synthesis=ProjectSynthesisSettings(engine="fake", voice="fake-voice"),
            composition=CompositionOptions(target_lufs=-18.0),
            export=ExportOptions(format="m4a", output=output_path, bitrate="128k"),
        ),
    )

    first = app.projects.build(project)
    assert first.output_path == output_path
    assert first.output_path is not None
    assert first.output_path.is_file()
    composed = app.projects.compose(project)
    assert composed.loudness is not None
    assert composed.loudness.target_lufs == -18.0
    direct_export = app.projects.export(project)
    assert direct_export.output_path == output_path
    assert direct_export.format == "m4a"
    assert app.projects.status(project).stage("output").state == "current"
    cache_files = sorted((project.root / "synthesis" / "cache").glob("*.wav"))
    master_path = project.root / "composition" / "master.wav"
    composition_state_path = project.root / "composition" / "state.json"
    original_composition_id = json.loads(composition_state_path.read_text(encoding="utf-8"))[
        "composition_id"
    ]

    updated = app.projects.update_settings(
        project,
        ProjectSettingsPatch(composition=CompositionOptions(target_lufs=-20.0)),
    )
    assert updated.export == configured.export
    stale = app.projects.status(project)
    assert stale.stage("synthesis").state == "current"
    assert stale.stage("composition").reason == "composition.stale.project_settings_changed"
    rebuilt = app.projects.build(project)
    actions = {item.stage: item.action for item in rebuilt.operations}
    assert actions["synthesis"] == "skipped"
    assert actions["composition"] == "rebuilt"
    assert actions["export"] == "skipped"
    updated_composition_id = json.loads(composition_state_path.read_text(encoding="utf-8"))[
        "composition_id"
    ]
    assert updated_composition_id != original_composition_id
    composed_master = master_path.read_bytes()
    assert sorted((project.root / "synthesis" / "cache").glob("*.wav")) == cache_files
    assert app.projects.status(project).stage("output").state == "current"
    updated = app.projects.update_settings(
        project,
        ProjectSettingsPatch(
            export=ExportOptions(format="m4a", output=output_path, bitrate="256k")
        ),
    )
    assert updated.composition is not None
    stale = app.projects.status(project)
    assert stale.stage("synthesis").state == "current"
    assert stale.stage("composition").state == "current"
    assert stale.stage("output").reason == "output.stale.project_settings_changed"
    rebuilt = app.projects.build(project)
    actions = {item.stage: item.action for item in rebuilt.operations}
    assert actions["synthesis"] == "skipped"
    assert actions["composition"] == "skipped"
    assert actions["export"] == "rebuilt"
    assert master_path.read_bytes() == composed_master
    assert sorted((project.root / "synthesis" / "cache").glob("*.wav")) == cache_files
    direct_export = app.projects.export(project)
    assert direct_export.output_path == output_path
    assert app.projects.status(project).stage("output").state == "current"
