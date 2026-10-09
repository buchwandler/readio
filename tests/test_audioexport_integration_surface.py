from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from readio.api import (
    AudiobookExportOptions,
    ExportOptions,
    ProjectBatchExportItem,
    ProjectBatchExportResult,
    ProjectSettings,
    Readio,
)
from readio.api import audiobooks as audiobook_api
from readio.api import projects as projects_api
from readio.cli import _cmd_audiobook_export, _cmd_export, _cmd_project_settings, build_parser
from readio.config import ReadioConfig
from readio.integrations import audioexport as audioexport_loader
from readio.project import init_project
from readio.project_settings import project_settings_from_manifest, with_project_settings


def _settings_project(tmp_path: Path):
    source = tmp_path / "book.txt"
    source.write_text("Hello world.", encoding="utf-8")
    return init_project(source, tmp_path / "book.readio")


def test_profile_options_preserve_legacy_defaults_and_track_format_selection() -> None:
    export_default = ExportOptions()
    assert export_default.format == "wav"
    assert export_default.profile is None
    assert not export_default.format_explicit

    assert ExportOptions(format="wav").format_explicit
    assert ExportOptions(format="mp3", profile=Path("export.toml")).format_explicit
    profile_only = ExportOptions(profile=Path("export.toml"))
    assert profile_only.format == "wav"
    assert not profile_only.format_explicit
    batch_options = ExportOptions(
        profile=Path("export.toml"), all_outputs=True, out_dir=Path("renders")
    )
    assert batch_options.all_outputs
    assert batch_options.out_dir == Path("renders")
    assert replace(batch_options, bitrate="128k").all_outputs

    audiobook_default = AudiobookExportOptions()
    assert audiobook_default.format == "m4b"
    assert not audiobook_default.format_explicit
    assert AudiobookExportOptions(profile=Path("export.toml")).format == "m4b"
    assert AudiobookExportOptions(format="m4b").format_explicit


def test_project_settings_cli_accepts_saved_audioexport_profiles() -> None:
    args = build_parser().parse_args(
        [
            "project",
            "settings",
            "set",
            "--export-profile",
            "export.toml",
            "--audiobook-profile",
            "audiobook.toml",
        ]
    )
    assert args.export_profile == Path("export.toml")
    assert args.audiobook_profile == Path("audiobook.toml")


def test_project_settings_profile_only_clears_implicit_format_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    captured = {}
    saved = ProjectSettings(export=ExportOptions(format="wav"))

    class Projects:
        def open(self, path):
            return path

        def settings(self, _project):
            return saved

        def update_settings(self, _project, patch):
            captured["export"] = patch.export
            return ProjectSettings(export=patch.export)

    monkeypatch.setattr("readio.cli._api_for", lambda _args: SimpleNamespace(projects=Projects()))
    profile = Path("export.toml")
    args = build_parser().parse_args(
        ["project", "settings", "set", "book.readio", "--export-profile", str(profile)]
    )

    assert _cmd_project_settings(args) == 0
    assert captured["export"].profile == profile
    assert not captured["export"].format_explicit
    capsys.readouterr()


def test_dataclass_replace_preserves_profile_format_selection() -> None:
    profile_only = ExportOptions(profile=Path("saved.toml"))
    replaced = replace(profile_only, bitrate="128k")
    assert replaced.profile == Path("saved.toml")
    assert not replaced.format_explicit

    explicit = AudiobookExportOptions(format="m4b", profile=Path("saved.toml"))
    replaced_audiobook = replace(explicit, title="Override")
    assert replaced_audiobook.format_explicit
    assert replaced_audiobook.profile == Path("saved.toml")


def test_profile_settings_round_trip_relative_paths_and_omitted_format(tmp_path: Path) -> None:
    project = _settings_project(tmp_path)
    settings = ProjectSettings(
        export=ExportOptions(profile=Path("settings/export.toml")),
        audiobook_export=AudiobookExportOptions(profile=Path("settings/book.toml")),
    )

    changed = with_project_settings(project.manifest, settings, project.root)
    persisted = changed.to_dict()["settings"]
    assert persisted["export"]["profile"] == "settings/export.toml"
    assert "format" not in persisted["export"]
    assert persisted["audiobook_export"]["profile"] == "settings/book.toml"
    assert "format" not in persisted["audiobook_export"]

    restored = project_settings_from_manifest(changed, project.root)
    assert restored.export.profile == project.root / "settings/export.toml"
    assert restored.export.format == "wav"
    assert not restored.export.format_explicit
    assert restored.audiobook_export.profile == project.root / "settings/book.toml"
    assert restored.audiobook_export.format == "m4b"
    assert not restored.audiobook_export.format_explicit


def test_explicit_profile_settings_format_is_persisted(tmp_path: Path) -> None:
    project = _settings_project(tmp_path)
    settings = ProjectSettings(
        export=ExportOptions(format="flac", profile=Path("export.toml")),
    )
    changed = with_project_settings(project.manifest, settings, project.root)
    assert changed.to_dict()["settings"]["export"]["format"] == "flac"
    restored = project_settings_from_manifest(changed, project.root).export
    assert restored.format == "flac"
    assert restored.format_explicit


def test_generic_api_profile_precedence_saved_then_explicit(tmp_path: Path) -> None:
    project = _settings_project(tmp_path)
    settings = ProjectSettings(export=ExportOptions(profile=Path("saved/export.toml")))
    manifest = with_project_settings(project.manifest, settings, project.root)
    project.manifest = manifest
    internal = project

    saved = projects_api._export_options(internal, ExportOptions(format="mp3"))
    assert saved.profile == project.root / "saved/export.toml"
    assert saved.format == "mp3"
    assert saved.format_explicit

    explicit_profile = Path("caller/export.toml")
    explicit = projects_api._export_options(internal, ExportOptions(profile=explicit_profile))
    assert explicit.profile == explicit_profile
    assert not explicit.format_explicit


def test_saved_export_options_merge_with_invocation_and_batch_overrides(tmp_path: Path) -> None:
    project = _settings_project(tmp_path)
    settings = ProjectSettings(
        export=ExportOptions(
            format="mp3",
            output=Path("saved/profile.mp3"),
            bitrate="96k",
            profile=Path("saved/export.toml"),
        )
    )
    project.manifest = with_project_settings(project.manifest, settings, project.root)

    inherited = projects_api._export_options(project, ExportOptions())
    assert inherited.profile == project.root / "saved/export.toml"
    assert inherited.output == project.root / "saved/profile.mp3"
    assert inherited.bitrate == "96k"
    assert inherited.format == "mp3" and inherited.format_explicit

    overridden = projects_api._export_options(
        project,
        ExportOptions(output=Path("caller/custom.mp3"), bitrate="128k"),
    )
    assert overridden.output == Path("caller/custom.mp3")
    assert overridden.bitrate == "128k"
    assert overridden.profile == inherited.profile

    batch = projects_api._export_options(
        project,
        ExportOptions(all_outputs=True, out_dir=Path("renders")),
    )
    assert batch.all_outputs and batch.out_dir == Path("renders")
    assert batch.profile == inherited.profile
    assert batch.output is None
    assert not batch.format_explicit


def test_audiobook_api_profile_precedence_saved_then_explicit(tmp_path: Path) -> None:
    project = _settings_project(tmp_path)
    settings = ProjectSettings(
        audiobook_export=AudiobookExportOptions(profile=Path("saved/book.toml"), title="Saved")
    )
    manifest = with_project_settings(project.manifest, settings, project.root)
    project.manifest = manifest
    internal = project

    saved = audiobook_api._export_options(internal, AudiobookExportOptions(title="Override"))
    assert saved.profile == project.root / "saved/book.toml"
    assert saved.title == "Override"

    explicit_profile = Path("caller/book.toml")
    explicit = audiobook_api._export_options(
        internal, AudiobookExportOptions(profile=explicit_profile)
    )
    assert explicit.profile == explicit_profile
    assert explicit.title == "Saved"


def test_profile_cli_distinguishes_omitted_format_and_resolves_profile_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    class Projects:
        def export(self, project, options):
            captured["project"] = project
            captured["options"] = options
            return SimpleNamespace(output_path=tmp_path / "book.wav")

    class Audiobooks:
        def export(self, project, options):
            captured["audiobook_project"] = project
            captured["audiobook_options"] = options
            return SimpleNamespace(output_path=tmp_path / "book.m4b")

    monkeypatch.setattr(
        "readio.cli._api_for",
        lambda _args: SimpleNamespace(projects=Projects(), audiobooks=Audiobooks()),
    )

    profile = tmp_path / "export.toml"
    generic = build_parser().parse_args(["export", "book.readio", "--profile", "export.toml"])
    assert generic.format is None
    assert _cmd_export(generic) == 0
    generic_options = captured["options"]
    assert isinstance(generic_options, ExportOptions)
    assert generic_options.format == "wav"
    assert not generic_options.format_explicit
    assert generic_options.profile == profile.resolve()
    capsys.readouterr()
    batch = build_parser().parse_args(
        [
            "export",
            "book.readio",
            "--profile",
            "export.toml",
            "--all",
            "--out-dir",
            "renders",
        ]
    )
    assert _cmd_export(batch) == 0
    batch_options = captured["options"]
    assert isinstance(batch_options, ExportOptions)
    assert batch_options.all_outputs
    assert batch_options.out_dir == Path("renders")
    assert not batch_options.format_explicit
    capsys.readouterr()

    explicit = build_parser().parse_args(
        ["export", "book.readio", "--profile", "export.toml", "--format", "mp3"]
    )
    assert _cmd_export(explicit) == 0
    explicit_options = captured["options"]
    assert isinstance(explicit_options, ExportOptions)
    assert explicit_options.format == "mp3"
    assert explicit_options.format_explicit
    capsys.readouterr()

    audiobook = build_parser().parse_args(
        ["audiobook", "export", "book.readio", "--profile", "export.toml"]
    )
    assert audiobook.format is None
    assert _cmd_audiobook_export(audiobook) == 0
    audiobook_options = captured["audiobook_options"]
    assert isinstance(audiobook_options, AudiobookExportOptions)
    assert audiobook_options.format == "m4b"
    assert not audiobook_options.format_explicit
    assert audiobook_options.profile == profile.resolve()


def test_profile_batch_export_is_exposed_by_project_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _settings_project(tmp_path)
    profile = tmp_path / "batch.toml"
    output = tmp_path / "renders" / "book.mp3"
    raw = {
        "success": False,
        "outputs": [
            {
                "format": "mp3",
                "path": output,
                "status": "encoded",
                "export_id": "sha256:one",
                "output_sha256": "one",
            },
            {
                "format": "flac",
                "path": tmp_path / "renders" / "book.flac",
                "status": "failed",
                "export_id": "sha256:two",
                "error_code": "audioexport.encoding_failed",
                "error_message": "injected failure",
            },
        ],
    }
    monkeypatch.setattr(projects_api, "export_profile_batch", lambda *_args, **_kwargs: raw)
    app = Readio(ReadioConfig())
    events = []

    result = app.projects.export(
        project.root,
        ExportOptions(profile=profile, all_outputs=True, out_dir=tmp_path / "renders"),
        on_event=events.append,
    )

    assert isinstance(result, ProjectBatchExportResult)
    assert not result.success
    assert [item.status for item in result.outputs] == ["encoded", "failed"]
    assert result.outputs[1].error_code == "audioexport.encoding_failed"
    serialized = json.loads(json.dumps(result.to_dict()))
    assert serialized["outputs"][0]["output_path"] == str(output)
    completed = next(event for event in events if event.kind == "stage.completed")
    assert completed.details == {"items": 2, "failed": 1}


def test_batch_export_cli_prints_ordered_rows_and_returns_failure_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _settings_project(tmp_path)
    app = Readio(ReadioConfig())
    result = ProjectBatchExportResult(
        project=app.projects.open(project.root),
        outputs=(
            ProjectBatchExportItem(
                format="mp3",
                output_path=tmp_path / "renders" / "one.mp3",
                status="failed",
                error_code="audioexport.encoding_failed",
                error_message="injected failure",
            ),
            ProjectBatchExportItem(
                format="flac",
                output_path=tmp_path / "renders" / "two.flac",
                status="not_attempted",
            ),
        ),
        success=False,
    )

    class Projects:
        def export(self, *_args, **_kwargs):
            return result

    monkeypatch.setattr("readio.cli._api_for", lambda _args: SimpleNamespace(projects=Projects()))
    args = build_parser().parse_args(
        ["export", str(project.root), "--profile", str(tmp_path / "batch.toml"), "--all"]
    )

    assert _cmd_export(args) == 1
    assert capsys.readouterr().out.splitlines() == [
        f"failed\tMP3\t{tmp_path / 'renders' / 'one.mp3'}",
        f"not_attempted\tFLAC\t{tmp_path / 'renders' / 'two.flac'}",
    ]


def test_audioexport_import_is_lazy_and_missing_extra_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = ModuleType("audioexport")
    calls: list[str] = []
    monkeypatch.setattr(
        audioexport_loader,
        "import_module",
        lambda name: calls.append(name) or sentinel,
    )
    assert calls == []
    assert audioexport_loader.load_audioexport() is sentinel
    assert calls == ["audioexport"]

    missing = ModuleNotFoundError("No module named audioexport", name="audioexport")

    def raise_missing(_name: str):
        raise missing

    monkeypatch.setattr(audioexport_loader, "import_module", raise_missing)
    with pytest.raises(
        audioexport_loader.AudioExportUnavailableError, match=r"readio\[audioexport\]"
    ) as error:
        audioexport_loader.load_audioexport()
    assert error.value.code == "audioexport.dependency_missing"
    assert error.value.__cause__ is missing


def test_audioexport_loader_does_not_hide_missing_transitive_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = ModuleNotFoundError("No module named dependency", name="audioexport_dependency")

    def raise_dependency(_name: str):
        raise missing

    monkeypatch.setattr(audioexport_loader, "import_module", raise_dependency)
    with pytest.raises(ModuleNotFoundError) as error:
        audioexport_loader.load_audioexport()
    assert error.value is missing


def test_audioexport_import_does_not_import_readio(tmp_path: Path) -> None:
    if importlib.util.find_spec("audioexport") is None:
        pytest.skip("AudioExport is not installed")
    script = """
import sys

class RejectReadioImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "readio" or fullname.startswith("readio."):
            raise AssertionError(f"AudioExport attempted to import {fullname}")

sys.meta_path.insert(0, RejectReadioImports())
import audioexport
"""
    subprocess.run([sys.executable, "-c", script], cwd=tmp_path, check=True)
