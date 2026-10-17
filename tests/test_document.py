from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from readio import document


class FakeStages:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.converted = SimpleNamespace(ssmd="<ssmd>hello</ssmd>")
        self.compiled = SimpleNamespace(plan=object())
        self.profile = object()
        self.render_plan = object()
        self.preflight = object()
        self.rendered = object()
        self.composed = object()
        self.exported = object()

        def convert(source: Path, *, options: object) -> object:
            self.calls.append(("convert", {"source": source, "options": options}))
            return self.converted

        def compile_document(text: str, **kwargs: Any) -> object:
            self.calls.append(("plan", {"text": text, **kwargs}))
            kwargs["on_progress"](SimpleNamespace(current=2, total=3, message="planning"))
            return self.compiled

        def load_profile(profile: Path) -> object:
            self.calls.append(("load-profile", {"profile": profile}))
            return self.profile

        def build_render_plan(plan: object, profile: object) -> object:
            self.calls.append(("render-plan", {"plan": plan, "profile": profile}))
            return self.render_plan

        def save_plan(plan: object, path: Path) -> Path:
            self.calls.append(("save-renderplan", {"plan": plan, "path": path}))
            path.write_text("renderplan", encoding="utf-8")
            return path

        def preflight(plan: object, output: Path, **kwargs: Any) -> object:
            self.calls.append(("preflight", {"plan": plan, "output": output, **kwargs}))
            return self.preflight

        def save_preflight(report: object, path: Path) -> Path:
            self.calls.append(("save-preflight", {"report": report, "path": path}))
            path.write_text("preflight", encoding="utf-8")
            return path

        def render(plan: object, **kwargs: Any) -> object:
            self.calls.append(("synth", {"plan": plan, **kwargs}))
            Path(kwargs["output_dir"]).mkdir(parents=True, exist_ok=True)
            return self.rendered

        def dump_utterplan(plan: object, path: Path) -> None:
            self.calls.append(("save-utterplan", {"plan": plan, "path": path}))
            path.write_text("utterplan", encoding="utf-8")

        def compose(plan: object, **kwargs: Any) -> object:
            self.calls.append(("compose", {"plan": plan, **kwargs}))
            Path(kwargs["output"]).write_bytes(b"wav")
            return self.composed

        def encode(source: Path, output: Path, **kwargs: Any) -> object:
            self.calls.append(("export", {"source": source, "output": output, **kwargs}))
            output.write_bytes(b"encoded")
            return self.exported

        monkeypatch.setattr(document.ssmdconvert, "convert_document", convert)
        monkeypatch.setattr(document.utterplan, "compile_document", compile_document)
        monkeypatch.setattr(document.voicerender, "load_render_profile", load_profile)
        monkeypatch.setattr(document.utterplan, "build_render_plan", build_render_plan)
        monkeypatch.setattr(document.renderplan, "save_plan", save_plan)
        monkeypatch.setattr(document.voicerender, "preflight_render_plan", preflight)
        monkeypatch.setattr(document.renderplan, "save_preflight", save_preflight)
        monkeypatch.setattr(document.voicerender, "render_renderplan", render)
        monkeypatch.setattr(document.utterplan.toml_codec, "dump", dump_utterplan)
        monkeypatch.setattr(document.audiocompose, "compose_renderplan", compose)
        monkeypatch.setattr(document.audioexport, "encode", encode)


def _request(tmp_path: Path, **kwargs: Any) -> document.DocumentRenderRequest:
    source = tmp_path / "story.txt"
    source.write_text("A small story.", encoding="utf-8")
    return document.DocumentRenderRequest(
        source=source,
        profile=tmp_path / "narration.renderprofile.toml",
        **kwargs,
    )


def test_render_document_forwards_native_results_options_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stages = FakeStages(monkeypatch)
    artifacts = tmp_path / "artifacts"
    output = tmp_path / "story.mp3"
    cover = tmp_path / "cover.jpg"
    progress: list[document.ProgressEvent] = []
    request = _request(
        tmp_path,
        artifacts=artifacts,
        output=output,
        speech_selector="2,4-5",
        export_sample_rate=48000,
        export_channels=1,
        export_bitrate="256k",
        metadata={"title": "Story"},
        cover=cover,
        force=True,
    )

    result = document.render_document(request, on_progress=progress.append)

    assert [
        name
        for name, _ in stages.calls
        if name not in {"load-profile", "save-utterplan", "save-renderplan", "save-preflight"}
    ] == ["convert", "plan", "render-plan", "preflight", "synth", "compose", "export"]
    assert result.conversion_result is stages.converted
    assert result.compile_result is stages.compiled
    assert result.renderplan is stages.render_plan
    assert result.preflight_report is stages.preflight
    assert result.render_result is stages.rendered
    assert result.compose_result is stages.composed
    assert result.export_result is stages.exported
    assert result.output == output
    assert (
        result.ssmd_path is not None
        and result.ssmd_path.read_text(encoding="utf-8") == stages.converted.ssmd
    )
    assert (
        result.utterplan_path is not None
        and result.utterplan_path.read_text(encoding="utf-8") == "utterplan"
    )
    assert (
        result.renderplan_path is not None
        and result.renderplan_path.read_text(encoding="utf-8") == "renderplan"
    )
    assert (
        result.preflight_path is not None
        and result.preflight_path.read_text(encoding="utf-8") == "preflight"
    )
    assert result.composed_wav is not None and result.composed_wav.read_bytes() == b"wav"
    assert output.read_bytes() == b"encoded"

    synth_kwargs = next(kwargs for name, kwargs in stages.calls if name == "synth")
    assert synth_kwargs["speech_numbers"] == (2, 4, 5)
    assert synth_kwargs["force"] is True
    compose_kwargs = next(kwargs for name, kwargs in stages.calls if name == "compose")
    assert compose_kwargs["output_policy"].sample_rate == 48000
    assert compose_kwargs["output_policy"].channels == 1
    export_kwargs = next(kwargs for name, kwargs in stages.calls if name == "export")
    assert export_kwargs["format"] == "mp3"
    assert export_kwargs["bitrate"] == "256k"
    assert export_kwargs["metadata"] == {"title": "Story"}
    assert export_kwargs["cover"] == cover
    assert export_kwargs["force"] is True
    assert any(event.stage == "plan" and event.kind == "progress" for event in progress)
    assert any(event.kind == "stage.started" for event in progress)
    assert result.artifacts_dir == artifacts
    assert not (artifacts / ".readio").exists()


@pytest.mark.parametrize(
    ("stop_after", "expected"),
    [
        ("convert", ["convert"]),
        ("plan", ["convert", "plan"]),
        ("render-plan", ["convert", "plan", "render-plan"]),
        ("synth", ["convert", "plan", "render-plan", "synth"]),
        ("compose", ["convert", "plan", "render-plan", "synth", "compose"]),
        ("export", ["convert", "plan", "render-plan", "synth", "compose", "export"]),
    ],
)
def test_render_document_stops_after_each_requested_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop_after: str,
    expected: list[str],
) -> None:
    stages = FakeStages(monkeypatch)
    request = _request(tmp_path, artifacts=tmp_path / "artifacts", stop_after=stop_after)

    document.render_document(request)

    observed = [
        name
        for name, _ in stages.calls
        if name in {"convert", "plan", "render-plan", "synth", "compose", "export"}
    ]
    assert observed == expected


def test_temporary_artifacts_are_removed_after_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeStages(monkeypatch)
    result = document.render_document(_request(tmp_path, stop_after="convert"))

    assert result.ssmd_path is not None
    assert not result.artifacts_dir.exists()


def test_failure_keeps_completed_persistent_artifacts_and_propagates_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stages = FakeStages(monkeypatch)
    failure = RuntimeError("planner failure")

    def fail(*_args: object, **_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(document.utterplan, "compile_document", fail)
    artifacts = tmp_path / "artifacts"
    request = _request(tmp_path, artifacts=artifacts)

    with pytest.raises(RuntimeError) as caught:
        document.render_document(request)

    assert caught.value is failure
    assert (artifacts / "story.ssmd.md").read_text(encoding="utf-8") == stages.converted.ssmd
    assert not (artifacts / "story.utterplan.toml").exists()


def test_clean_artifacts_refuses_unknown_entries_without_removing_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeStages(monkeypatch)
    source = tmp_path / "story.txt"
    source.write_text("Story", encoding="utf-8")
    artifacts = tmp_path / "story.render"
    artifacts.mkdir()
    unexpected = artifacts / "notes.txt"
    unexpected.write_text("keep", encoding="utf-8")
    request = _request(tmp_path, keep_artifacts=True, clean_artifacts=True, stop_after="convert")

    with pytest.raises(ValueError, match="unexpected artifact entries"):
        document.render_document(request)

    assert unexpected.read_text(encoding="utf-8") == "keep"


def test_clean_artifacts_only_removes_expected_default_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeStages(monkeypatch)
    source = tmp_path / "story.txt"
    source.write_text("Story", encoding="utf-8")
    artifacts = tmp_path / "story.render"
    artifacts.mkdir()
    old_artifact = artifacts / "story.ssmd.md"
    old_artifact.write_text("old", encoding="utf-8")

    result = document.render_document(
        _request(tmp_path, keep_artifacts=True, clean_artifacts=True, stop_after="convert")
    )

    assert result.ssmd_path == old_artifact
    assert old_artifact.read_text(encoding="utf-8") == "<ssmd>hello</ssmd>"
    assert set(artifacts.iterdir()) == {old_artifact}


def test_custom_artifact_directory_cannot_be_recursively_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeStages(monkeypatch)
    custom = tmp_path / "custom"
    custom.mkdir()
    keep = custom / "notes.txt"
    keep.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="expected <source-stem>.render"):
        document.render_document(
            _request(
                tmp_path,
                artifacts=custom,
                clean_artifacts=True,
                stop_after="convert",
            )
        )

    assert keep.read_text(encoding="utf-8") == "keep"


def test_render_directory_symlink_is_refused_before_voice_render_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stages = FakeStages(monkeypatch)
    artifacts = tmp_path / "story.render"
    artifacts.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (artifacts / "renders").symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="real directory"):
        document.render_document(_request(tmp_path, keep_artifacts=True, force=True))

    assert not any(name == "preflight" for name, _ in stages.calls)
    assert not list(external.iterdir())
