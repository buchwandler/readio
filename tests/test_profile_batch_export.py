from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from readio.integrations.audioexport import AudioExportIntegrationError
from readio.project import init_project
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
