from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import config as config_internal
from readio import ingest as ingest_internal
from readio import templates as templates_internal
from readio.api import (
    AudioFormatDiagnostic,
    DependencyDiagnostic,
    DiscoveryOptions,
    DoctorReport,
    EngineDiagnostic,
    InputError,
    InvalidRequestError,
    OutputError,
    PathDiagnostic,
    Readio,
    TemplateValidationResult,
)
from readio.config import LanguageSettings, PathSettings, ReadioConfig


def _app(tmp_path: Path) -> tuple[Readio, PathSettings]:
    paths = PathSettings(
        templates=tmp_path / "templates",
        ingest=tmp_path / "ingest",
        output=tmp_path / "output",
    )
    return Readio(ReadioConfig(paths=paths)), paths


def test_configuration_service_load_save_set_and_atomic_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, _paths = _app(tmp_path)
    service = app.configuration
    config_path = tmp_path / "config.toml"

    assert service.defaults() == ReadioConfig()
    assert service.validate(service.defaults()) == service.defaults()
    assert service.save(service.defaults(), path=config_path) == config_path
    assert service.load(config_path) == ReadioConfig()

    with pytest.raises(OutputError) as error:
        service.save(service.defaults(), path=config_path)
    assert error.value.code == "config.exists"

    updated = service.set_value("reader.speed", "1.25", path=config_path)
    assert updated.reader.speed == 1.25
    assert service.load(config_path).reader.speed == 1.25
    assert app.config.reader.speed == 1.0
    persisted_before_failure = config_path.read_text(encoding="utf-8")

    def fail_replace(_source: str, _target: Path) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(config_internal.os, "replace", fail_replace)
    changed = replace(updated, reader=replace(updated.reader, speed=1.5))
    with pytest.raises(OutputError) as error:
        service.save(changed, path=config_path, overwrite=True)
    assert error.value.code == "config.write_failed"
    assert config_path.read_text(encoding="utf-8") == persisted_before_failure
    assert service.load(config_path).reader.speed == 1.25
    assert list(tmp_path.glob(".config.toml.*")) == []


def test_configuration_service_profiles_persist_without_mutating_app_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("READIO_CONFIG", str(config_path))
    app, _paths = _app(tmp_path)
    settings = LanguageSettings(model="test-model", voice="test-voice")

    saved = app.configuration.set_language_profile(
        "DE_at", settings, validate_runtime=False
    )

    assert saved == settings
    second_settings = LanguageSettings(model="french-model")
    app.configuration.set_language_profile(
        "fr", second_settings, validate_runtime=False
    )
    assert app.configuration.language_profiles() == {}
    assert app.configuration.language_profile("de-AT") is None
    persisted = app.configuration.load()
    assert persisted.languages == {"de-at": settings, "fr": second_settings}

    fresh_app = Readio(persisted)
    assert fresh_app.configuration.language_profile("de_AT") == settings
    fresh_app.configuration.reset_language_profile("DE-at")
    assert fresh_app.configuration.load().languages == {"fr": second_settings}

    with pytest.raises(InvalidRequestError) as error:
        fresh_app.configuration.reset_language_profile("de-at")
    assert error.value.code == "config.language_profile_not_found"
    fresh_app.configuration.reset_language_profile("fr")
    assert fresh_app.configuration.load().languages == {}


def test_configuration_profile_runtime_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("READIO_CONFIG", str(config_path))
    app, _paths = _app(tmp_path)
    calls: dict[str, object] = {}

    def resolve_selector(voice: str, **kwargs: object) -> SimpleNamespace:
        calls["selector"] = (voice, kwargs)
        return SimpleNamespace(
            selector="de:thorsten",
            language="de",
            model="de-model",
            source="upstream",
            voice="thorsten",
        )

    def get_model(model_id: str, **kwargs: object) -> tuple[SimpleNamespace, None]:
        calls["model"] = (model_id, kwargs)
        return SimpleNamespace(
            source="upstream",
            default_voice="thorsten",
            qualities=("fp16", "fp32"),
        ), None

    def validate_profile(
        language: str, settings: LanguageSettings, model: SimpleNamespace
    ) -> LanguageSettings:
        calls["validated"] = (language, settings, model)
        return settings

    monkeypatch.setattr(
        "readio.api.configuration.resolve_voice_selector", resolve_selector
    )
    monkeypatch.setattr("readio.api.configuration.get_model_info", get_model)
    monkeypatch.setattr(
        "readio.api.configuration.validate_language_settings", validate_profile
    )

    result = app.configuration.set_language_profile(
        "de-DE",
        LanguageSettings(voice="de:thorsten"),
        discovery=DiscoveryOptions(offline=True, preference="upstream"),
    )

    assert result == LanguageSettings(
        model="de-model",
        source="upstream",
        quality="fp32",
        voice="thorsten",
    )
    assert calls["selector"][1]["offline"] is True
    assert calls["selector"][1]["preference"] == "upstream"
    assert calls["model"][1]["backend"] is None
    assert calls["validated"][0] == "de"
    assert app.configuration.load().languages == {"de": result}



def test_configuration_service_maps_invalid_values_to_public_errors(tmp_path: Path) -> None:
    app, _paths = _app(tmp_path)

    with pytest.raises(InvalidRequestError) as error:
        app.configuration.set_value("reader.speed", "0", path=tmp_path / "config.toml")
    assert error.value.code == "config.set_failed"

    invalid = replace(app.configuration.defaults(), reader=replace(app.config.reader, speed=0))
    with pytest.raises(InvalidRequestError) as error:
        app.configuration.validate(invalid)
    assert error.value.code == "config.invalid"


def test_template_service_manages_templates_and_rejects_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, paths = _app(tmp_path)

    with pytest.raises(InputError):
        app.templates.list()
    assert not paths.templates.exists()

    seeded = app.templates.seed()
    assert {path.stem for path in seeded} == set(templates_internal.packaged_template_names())
    assert app.templates.path("briefing") == paths.templates / "briefing.ssmd"
    assert app.templates.show("briefing")
    assert all(isinstance(item, TemplateValidationResult) and item.ok for item in (
        app.templates.validate("briefing"),
        app.templates.validate("dialogue"),
        app.templates.validate("podcast"),
    ))

    content = templates_internal.packaged_template("briefing")
    custom = app.templates.add("custom", content=content)
    assert custom.read_text(encoding="utf-8") == content
    assert "custom" in {item.name for item in app.templates.list()}
    app.templates.remove("custom")
    assert "custom" not in {item.name for item in app.templates.list()}

    changed = app.templates.path("podcast")
    changed.write_text("custom content", encoding="utf-8")
    with pytest.raises(InvalidRequestError):
        app.templates.add("podcast", content=content)
    restored = app.templates.reset("podcast")
    assert restored == (changed,)
    assert changed.read_text(encoding="utf-8") == templates_internal.packaged_template("podcast")

    outside = tmp_path / "outside.ssmd"
    with pytest.raises(InvalidRequestError):
        app.templates.add("../outside", content=content)
    assert not outside.exists()
    with pytest.raises(InvalidRequestError):
        app.templates.path("../outside")

    outside.write_text(content, encoding="utf-8")
    (paths.templates / "escape.ssmd").symlink_to(outside)
    with pytest.raises(InvalidRequestError):
        app.templates.path("escape")


def test_template_overwrite_failure_preserves_original_and_cleans_temporary_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, paths = _app(tmp_path)
    app.templates.seed()
    target = paths.templates / "podcast.ssmd"
    original = target.read_text(encoding="utf-8")

    def fail_replace(_source: str, _target: Path) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(templates_internal.os, "replace", fail_replace)
    with pytest.raises(OutputError):
        app.templates.add("podcast", content="replacement", force=True)
    assert target.read_text(encoding="utf-8") == original
    assert list(paths.templates.glob(".podcast.ssmd.*")) == []


def test_ingest_service_is_read_only_until_create_and_rejects_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, paths = _app(tmp_path)

    assert app.ingest.directory() == paths.ingest
    assert app.ingest.list() == ()
    assert not paths.ingest.exists()
    with pytest.raises(InvalidRequestError):
        app.ingest.create(name="../escape.txt")
    assert not paths.ingest.exists()

    target = app.ingest.create(name="notes.txt")
    assert target.read_text(encoding="utf-8") == ""
    with pytest.raises(InvalidRequestError):
        app.ingest.create(name="notes.txt")

    app.templates.seed()
    copied = app.ingest.create(template="briefing")
    assert copied.suffix == ".ssmd"
    assert copied.read_text(encoding="utf-8") == templates_internal.packaged_template("briefing")
    assert {path.name for path in app.ingest.list()} == {"notes.txt", copied.name}


def test_ingest_template_copy_failure_does_not_leave_partial_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, paths = _app(tmp_path)
    app.templates.seed()

    def fail_replace(_source: str, _target: Path) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(ingest_internal.os, "replace", fail_replace)
    with pytest.raises(OutputError):
        app.ingest.create(name="atomic.ssmd", template="briefing")
    assert not (paths.ingest / "atomic.ssmd").exists()
    assert list(paths.ingest.glob(".atomic.ssmd.*")) == []


def test_diagnostics_are_typed_serializable_and_do_not_create_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("READIO_CONFIG", str(config_path))
    app, paths = _app(tmp_path)

    report = app.diagnostics.run()

    assert isinstance(report, DoctorReport)
    assert report.config_path == config_path
    assert report.config_exists is False
    assert all(isinstance(item, EngineDiagnostic) for item in report.engines)
    assert all(isinstance(item, DependencyDiagnostic) for item in report.dependencies)
    assert {"pykokoro", "piper"} <= {item.id for item in report.engines}
    assert {"utterplan", "audiocompose", "ssmd"} <= {
        item.id for item in report.dependencies
    }
    assert all(isinstance(item, AudioFormatDiagnostic) for item in report.audio_formats)
    assert {"wav", "mp3", "m4a", "ogg"} == {
        item.id for item in report.audio_formats
    }
    assert all(isinstance(item, PathDiagnostic) for item in report.paths)
    serialized = json.loads(json.dumps(report.to_dict()))
    assert serialized["config_path"] == str(config_path)
    assert {item["name"] for item in serialized["paths"]} == {"templates", "ingest", "output"}
    assert not paths.templates.exists()
    assert not paths.ingest.exists()
    assert not paths.output.exists()
    assert not config_path.exists()
