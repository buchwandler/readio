from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from audiobook_support import make_epub

from readio.audiobook import init_audiobook_project
from readio.integrations.audioexport import AudioExportIntegrationError
from readio.project import init_project
from readio.stages import audiobook_export as audiobook_stage
from readio.stages import export as export_stage


class FakeAudioExportError(Exception):
    def __init__(self, message: str, *, code: str = "audioexport.preflight_failed") -> None:
        super().__init__(message)
        self.code = code
        self.details: dict[str, str] = {}


@dataclass(frozen=True)
class FakeOutputSpec:
    format: str
    filename: str | None = None
    bitrate: str | None = "96k"
    use_cover: bool | None = None
    use_chapters: bool | None = None


@dataclass(frozen=True)
class FakeProfile:
    outputs: tuple[FakeOutputSpec, ...]
    metadata: Mapping[str, str] = field(default_factory=dict)
    cover: Path | None = None
    timeline: Path | None = None


@dataclass(frozen=True)
class FakeAudiobookMetadata:
    title: str
    author: str | None
    cover: Path | None = None
    cover_sha256: str | None = None


@dataclass(frozen=True)
class FakePreparedAudiobook:
    export_id: str
    master: Path
    timeline_sha256: str
    bitrate: str
    metadata: FakeAudiobookMetadata


class FakeAudioExport:
    __version__ = "test"
    AudioExportError = FakeAudioExportError

    def __init__(self, profile: FakeProfile) -> None:
        self.profile = profile
        self.preflight_formats: list[str] = []
        self.fail_preflight_format: str | None = None

    def load_profile(self, _path: Path) -> FakeProfile:
        return self.profile

    def resolve_output(
        self, profile: FakeProfile, spec: FakeOutputSpec, source_stem: str
    ) -> SimpleNamespace:
        return SimpleNamespace(
            format=spec.format,
            filename=spec.filename or f"{source_stem}.{spec.format}",
            bitrate=spec.bitrate,
            cover=profile.cover if spec.use_cover else None,
            timeline=profile.timeline if spec.use_chapters else None,
        )

    def preflight_profile(self, profile: FakeProfile, master: Path) -> list[SimpleNamespace]:
        spec = profile.outputs[0]
        self.preflight_formats.append(spec.format)
        if spec.format == self.fail_preflight_format:
            raise FakeAudioExportError(
                f"injected {spec.format} preflight failure",
                code="audioexport.preflight_failed",
            )
        return [self.resolve_output(profile, spec, Path(master).stem)]

    @staticmethod
    def normalize_bitrate(value: str | None, _format: str) -> str | None:
        return value

    @staticmethod
    def doctor() -> dict[str, object]:
        return {"tools": {}}


@dataclass(frozen=True)
class FakeResolvedAudiobookProfile:
    profile: FakeProfile
    resolved_output: SimpleNamespace
    metadata: Mapping[str, str]
    version: str
    audioexport: FakeAudioExport


def _project_with_master(tmp_path: Path):
    source = tmp_path / "book.txt"
    source.write_text("fixture", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    master = project.paths["composition_master"]
    master.parent.mkdir(parents=True, exist_ok=True)
    sf.write(master, np.zeros(16_000, dtype=np.float32), 16_000)
    return project


def _use_fake_audioexport(monkeypatch: pytest.MonkeyPatch, fake: FakeAudioExport) -> None:
    monkeypatch.setattr(export_stage, "load_audioexport", lambda: fake)
    monkeypatch.setattr("readio.integrations.audioexport.load_audioexport", lambda: fake)


def test_profile_batch_preflights_all_outputs_before_partial_encoding_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project_with_master(tmp_path)
    profile_path = tmp_path / "batch.toml"
    profile_path.write_text("test profile", encoding="utf-8")
    profile = FakeProfile(
        (
            FakeOutputSpec("mp3", "one.mp3"),
            FakeOutputSpec("flac", "two.flac"),
            FakeOutputSpec("opus", "three.opus"),
        )
    )
    fake = FakeAudioExport(profile)
    _use_fake_audioexport(monkeypatch, fake)
    fail_flac = True
    writes: list[str] = []

    def encode(_master: Path, target: Path, _resolved, **_kwargs):
        assert len(fake.preflight_formats) in {3, 6, 9}
        if target.suffix == ".flac" and fail_flac:
            raise RuntimeError("injected encode failure")
        target.write_bytes(target.suffix.encode("ascii"))
        writes.append(target.name)
        return SimpleNamespace(
            reused=False,
            export_id=f"fake:{target.stem}",
            manifest_path=target.with_suffix(target.suffix + ".json"),
        )

    monkeypatch.setattr(export_stage, "encode_resolved", encode)
    output_dir = tmp_path / "batch-output"

    first = export_stage.export_profile_batch(project, profile=profile_path, out_dir=output_dir)
    assert fake.preflight_formats == ["mp3", "flac", "opus"]
    assert writes == ["one.mp3"]
    assert [item["status"] for item in first["outputs"]] == [
        "encoded",
        "failed",
        "not_attempted",
    ]
    assert first["success"] is False
    assert (output_dir / "one.mp3").is_file()
    assert not (output_dir / "two.flac").exists()
    assert not (output_dir / "three.opus").exists()

    fail_flac = False
    second = export_stage.export_profile_batch(project, profile=profile_path, out_dir=output_dir)
    assert fake.preflight_formats == ["mp3", "flac", "opus"] * 2
    assert [item["status"] for item in second["outputs"]] == [
        "reused",
        "encoded",
        "encoded",
    ]
    assert second["success"] is True
    assert writes == ["one.mp3", "two.flac", "three.opus"]

    fake.profile = FakeProfile(
        (
            FakeOutputSpec("mp3", "one.mp3"),
            FakeOutputSpec("flac", "two.flac", bitrate="128k"),
            FakeOutputSpec("opus", "three.opus"),
        )
    )
    third = export_stage.export_profile_batch(project, profile=profile_path, out_dir=output_dir)
    assert fake.preflight_formats == ["mp3", "flac", "opus"] * 3
    assert [item["status"] for item in third["outputs"]] == [
        "reused",
        "encoded",
        "reused",
    ]
    assert writes == ["one.mp3", "two.flac", "three.opus", "two.flac"]


def test_profile_batch_preflight_failure_writes_no_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project_with_master(tmp_path)
    profile_path = tmp_path / "batch.toml"
    profile_path.write_text("test profile", encoding="utf-8")
    fake = FakeAudioExport(
        FakeProfile((FakeOutputSpec("mp3", "one.mp3"), FakeOutputSpec("flac", "two.flac")))
    )
    fake.fail_preflight_format = "flac"
    _use_fake_audioexport(monkeypatch, fake)
    writes: list[Path] = []
    monkeypatch.setattr(
        export_stage,
        "encode_resolved",
        lambda _master, target, _resolved, **_kwargs: writes.append(target),
    )
    output_dir = tmp_path / "batch-output"

    with pytest.raises(AudioExportIntegrationError) as failure:
        export_stage.export_profile_batch(project, profile=profile_path, out_dir=output_dir)

    assert failure.value.code == "audioexport.preflight_failed"
    assert fake.preflight_formats == ["mp3", "flac"]
    assert writes == []
    assert not output_dir.exists()


def test_profile_batch_rejects_profile_filename_outside_out_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project_with_master(tmp_path)
    profile_path = tmp_path / "batch.toml"
    profile_path.write_text("test profile", encoding="utf-8")
    fake = FakeAudioExport(FakeProfile((FakeOutputSpec("mp3", "../outside.mp3"),)))
    _use_fake_audioexport(monkeypatch, fake)
    writes: list[Path] = []
    monkeypatch.setattr(
        export_stage,
        "encode_resolved",
        lambda _master, target, _resolved, **_kwargs: writes.append(target),
    )
    output_dir = tmp_path / "batch-output"

    with pytest.raises(AudioExportIntegrationError) as failure:
        export_stage.export_profile_batch(project, profile=profile_path, out_dir=output_dir)

    assert failure.value.code == "readio.export.output_directory_escape"
    assert fake.preflight_formats == ["mp3"]
    assert writes == []
    assert not output_dir.exists()


def test_profile_batch_rejects_m4b_for_non_audiobook_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project_with_master(tmp_path)
    profile_path = tmp_path / "batch.toml"
    profile_path.write_text("test profile", encoding="utf-8")
    fake = FakeAudioExport(FakeProfile((FakeOutputSpec("m4b", "book.m4b"),)))
    _use_fake_audioexport(monkeypatch, fake)
    writes: list[Path] = []
    monkeypatch.setattr(
        export_stage,
        "encode_resolved",
        lambda _master, target, _resolved, **_kwargs: writes.append(target),
    )
    output_dir = tmp_path / "batch-output"

    with pytest.raises(AudioExportIntegrationError) as failure:
        export_stage.export_profile_batch(project, profile=profile_path, out_dir=output_dir)

    assert failure.value.code == "audiobook.export.not_audiobook"
    assert fake.preflight_formats == []
    assert writes == []
    assert not output_dir.exists()


def test_profile_batch_rejects_duplicate_targets_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project_with_master(tmp_path)
    profile_path = tmp_path / "batch.toml"
    profile_path.write_text("test profile", encoding="utf-8")
    fake = FakeAudioExport(
        FakeProfile((FakeOutputSpec("mp3", "same.mp3"), FakeOutputSpec("mp3", "same.mp3")))
    )
    _use_fake_audioexport(monkeypatch, fake)
    writes: list[Path] = []
    monkeypatch.setattr(
        export_stage,
        "encode_resolved",
        lambda _master, target, _resolved, **_kwargs: writes.append(target),
    )
    output_dir = tmp_path / "batch-output"

    with pytest.raises(AudioExportIntegrationError) as failure:
        export_stage.export_profile_batch(project, profile=profile_path, out_dir=output_dir)

    assert failure.value.code == "readio.export.profile_output_collision"
    assert fake.preflight_formats == ["mp3", "mp3"]
    assert writes == []
    assert not output_dir.exists()


def test_profile_batch_rejects_foreign_audiobook_timeline_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "book.epub"
    make_epub(source)
    project = init_audiobook_project(source, tmp_path / "book.ssmdbook")
    master = project.paths["composition_master"]
    master.parent.mkdir(parents=True, exist_ok=True)
    sf.write(master, np.zeros(16_000, dtype=np.float32), 16_000)
    timeline = project.paths["composition_timeline"]
    timeline.parent.mkdir(parents=True, exist_ok=True)
    timeline.write_text("verified timeline", encoding="utf-8")
    foreign_timeline = tmp_path / "foreign-timeline.json"
    foreign_timeline.write_text("foreign", encoding="utf-8")
    profile_path = tmp_path / "audiobook.toml"
    profile_path.write_text("test profile", encoding="utf-8")
    fake = FakeAudioExport(
        FakeProfile((FakeOutputSpec("m4b", "selected.m4b"),), timeline=foreign_timeline)
    )
    _use_fake_audioexport(monkeypatch, fake)
    output_dir = tmp_path / "batch-output"

    with pytest.raises(AudioExportIntegrationError) as failure:
        export_stage.export_profile_batch(project, profile=profile_path, out_dir=output_dir)

    assert failure.value.code == "audiobook.export.timeline_stale"
    assert fake.preflight_formats == []
    assert not output_dir.exists()


def test_profile_batch_routes_m4b_through_readio_audiobook_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "book.epub"
    make_epub(source)
    project = init_audiobook_project(source, tmp_path / "book.ssmdbook")
    master = project.paths["composition_master"]
    master.parent.mkdir(parents=True, exist_ok=True)
    sf.write(master, np.zeros(16_000, dtype=np.float32), 16_000)
    timeline = project.paths["composition_timeline"]
    timeline.parent.mkdir(parents=True, exist_ok=True)
    timeline.write_text("verified timeline", encoding="utf-8")
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"cover")
    profile_path = tmp_path / "audiobook.toml"
    profile_path.write_text("test profile", encoding="utf-8")

    spec = FakeOutputSpec("m4b", "selected.m4b", "96k", use_cover=True, use_chapters=True)
    generic_spec = FakeOutputSpec("mp3", "plain.mp3", use_cover=False, use_chapters=False)
    loaded_profile = FakeProfile((spec, generic_spec), cover=cover)
    fake = FakeAudioExport(loaded_profile)
    _use_fake_audioexport(monkeypatch, fake)
    resolved_profile = FakeProfile(
        (spec,),
        metadata={"title": "Profile title", "artist": "Profile author"},
        cover=cover,
        timeline=timeline.resolve(),
    )
    resolved = FakeResolvedAudiobookProfile(
        profile=resolved_profile,
        resolved_output=SimpleNamespace(
            format="m4b",
            filename="selected.m4b",
            bitrate="96k",
            cover=cover,
            timeline=timeline.resolve(),
        ),
        metadata={"title": "Profile title", "artist": "Profile author"},
        version="test",
        audioexport=fake,
    )
    prepared = FakePreparedAudiobook(
        "sha256:before-profile-identity",
        master,
        "sha256:timeline",
        "96k",
        FakeAudiobookMetadata("Profile title", "Profile author", cover, "sha256:cover"),
    )
    resolve_calls: list[dict[str, object]] = []

    def resolve_audiobook(_project, *_args, **kwargs):
        resolve_calls.append(kwargs)
        return resolved

    monkeypatch.setattr(audiobook_stage, "_resolve_audiobook_profile", resolve_audiobook)
    prepare_calls: list[dict[str, object]] = []

    def prepare_audiobook(_project, **kwargs):
        prepare_calls.append(kwargs)
        return prepared

    monkeypatch.setattr(audiobook_stage, "prepare_audiobook_export", prepare_audiobook)
    monkeypatch.setattr(
        audiobook_stage,
        "_profile_audiobook_export_identity",
        lambda *_args, **_kwargs: "sha256:readio-profile-identity",
    )
    calls: list[dict[str, object]] = []

    def export_audiobook(_project, **kwargs):
        calls.append(kwargs)
        kwargs["output"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["output"].write_bytes(b"m4b")
        return {
            "export_id": "sha256:readio-profile-identity",
            "output_sha256": "sha256:output",
        }

    monkeypatch.setattr(audiobook_stage, "export_audiobook_project", export_audiobook)
    generic_writes: list[Path] = []

    def encode_generic(_master: Path, target: Path, _resolved, **_kwargs):
        target.write_bytes(b"mp3")
        generic_writes.append(target)
        return SimpleNamespace(
            reused=False,
            export_id="audioexport:plain",
            manifest_path=target.with_suffix(target.suffix + ".json"),
        )

    monkeypatch.setattr(export_stage, "encode_resolved", encode_generic)
    output_dir = tmp_path / "batch-output"

    result = export_stage.export_profile_batch(project, profile=profile_path, out_dir=output_dir)

    assert result["success"] is True, result
    assert fake.preflight_formats == ["m4b", "mp3"]
    assert [item["format"] for item in result["outputs"]] == ["m4b", "mp3"]
    assert result["outputs"][0]["path"] == output_dir / "selected.m4b"
    assert result["outputs"][0]["status"] == "encoded"
    assert result["outputs"][1]["path"] == output_dir / "plain.mp3"
    assert result["outputs"][1]["status"] == "encoded"
    assert generic_writes == [output_dir / "plain.mp3"]
    generic_state = export_stage.output_state_for(project, output_dir / "plain.mp3")
    assert generic_state is not None
    assert generic_state["options"]["cover_sha256"] is None
    assert generic_state["options"]["timeline_sha256"] is None
    assert calls[0]["selected_profile_output"] is spec
    assert resolve_calls[0]["selected_output"] is spec
    assert prepare_calls[0]["cover"] == cover
    assert prepare_calls[0]["bitrate"] == "96k"
    assert spec.use_cover is True and spec.use_chapters is True
    assert generic_spec.use_cover is False and generic_spec.use_chapters is False
    assert calls[0]["loaded_profile"] is loaded_profile
    assert calls[0]["lock_held"] is True
