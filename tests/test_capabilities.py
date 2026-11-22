from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from readio import capabilities as capability_module
from readio.api import (
    EngineInfo,
    Readio,
    SourceChapter,
    SourceInspection,
    export_capabilities_to_dict,
    serialize_public,
)


def _engine(*, runnable: bool = True) -> EngineInfo:
    return EngineInfo(
        id="local",
        version="1.0",
        installed=True,
        runnable=runnable,
        features=("synthesis",),
    )


def test_aggregate_capabilities_use_normalized_read_only_public_data(monkeypatch) -> None:
    monkeypatch.setattr(capability_module, "distribution_version", lambda package: "1.2.3")
    monkeypatch.setattr(Readio, "list_engines", lambda self: (_engine(),))
    monkeypatch.setattr(
        capability_module,
        "supported_formats",
        lambda: {
            "m4b": SimpleNamespace(extension=".m4b"),
            "mp3": SimpleNamespace(extension=".mp3"),
        },
    )
    preflighted: list[str] = []

    def preflight(name: str) -> None:
        preflighted.append(name)
        if name == "m4b":
            raise RuntimeError("ffmpeg is unavailable")

    monkeypatch.setattr(capability_module, "preflight_export", preflight)
    before = set(Path.cwd().iterdir())

    result = Readio().capabilities()

    assert set(Path.cwd().iterdir()) == before
    assert result.workflow.conversion == "ready"
    assert result.workflow.planning == "ready"
    assert result.workflow.synthesis == "ready"
    assert result.workflow.composition == "ready"
    assert result.workflow.export == "ready"
    assert result.engines == (_engine(),)
    assert [(item.name, item.available) for item in result.export_formats] == [
        ("m4b", False),
        ("mp3", True),
    ]
    assert preflighted == ["m4b", "mp3"]

    serialized = serialize_public(result)
    assert serialized["schema"] == "readio.capabilities.v1"
    assert serialized["result"]["workflow"]["synthesis"] == "ready"


def test_missing_specialist_and_unknown_version_are_reported_without_failure(monkeypatch) -> None:
    def package_version(package: str) -> str | None:
        if package == "ssmdconvert":
            return None
        if package == "utterplan":
            raise RuntimeError("metadata store unavailable")
        return "1.2.3"

    monkeypatch.setattr(capability_module, "distribution_version", package_version)
    monkeypatch.setattr(Readio, "list_engines", lambda self: (_engine(),))
    monkeypatch.setattr(capability_module, "supported_formats", dict)

    result = Readio().capabilities()
    specialists = {item.package: item for item in result.specialists}

    assert result.workflow.conversion == "unavailable"
    assert result.workflow.planning == "unknown"
    assert specialists["ssmdconvert"].reason == "Distribution is not installed."
    assert specialists["utterplan"].state == "unknown"
    assert "Cannot inspect package metadata" in specialists["utterplan"].reason


def test_source_capabilities_inspect_one_source_without_creating_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "book.epub"
    source.write_bytes(b"epub")
    inspection = SourceInspection(
        source=source,
        kind="book",
        metadata={"title": "Example"},
        chapters=(SourceChapter(number=1, title="Chapter 1"),),
    )
    service = Readio()
    monkeypatch.setattr(service, "inspect_source", lambda _source: inspection)
    before = set(tmp_path.iterdir())

    result = service.source_capabilities(source)

    assert set(tmp_path.iterdir()) == before
    assert result.source is inspection
    assert result.chapter_selection is True
    assert result.persistent_workspace is True
    assert result.default_output_format == "m4b"
    assert serialize_public(result)["schema"] == "readio.source-capabilities.v1"


def test_missing_synthesis_engine_is_reported_as_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(capability_module, "distribution_version", lambda _package: "1.2.3")
    monkeypatch.setattr(Readio, "list_engines", lambda self: ())

    result = Readio().synthesis_capabilities()

    assert result.state == "unavailable"
    assert result.engines == ()
    assert result.reason == "VoiceRender reported no synthesis engines."
    assert serialize_public(result)["schema"] == "readio.synthesis-capabilities.v1"


def test_missing_voicerender_distribution_does_not_attempt_engine_discovery(monkeypatch) -> None:
    monkeypatch.setattr(capability_module, "distribution_version", lambda _package: None)
    monkeypatch.setattr(
        Readio,
        "list_engines",
        lambda self: (_ for _ in ()).throw(AssertionError("engine discovery should not run")),
    )

    result = Readio().synthesis_capabilities()

    assert result.state == "unavailable"
    assert result.reason == "Distribution is not installed."


def test_export_capabilities_are_versioned_even_when_no_formats_are_available(monkeypatch) -> None:
    monkeypatch.setattr(capability_module, "supported_formats", dict)

    result = Readio().export_capabilities()

    assert result == ()
    assert export_capabilities_to_dict(result) == {
        "schema": "readio.export-capabilities.v1",
        "result": {"formats": []},
    }
