from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from readio.api import RenderExecutionOptions
from readio.errors import ReadioStageError
from readio.intent import create_book_intent, create_document_intent
from readio.intent_resolver import ResolvedRenderIntent
from readio.render_service import IntentStageError, plan_render_intent, render_intent
from readio.workspace import WorkspaceRef

VARIANT = "utterplan/exact-variant-id"


def _resolved(
    tmp_path: Path, *, kind: str = "book", bundle: Path | None = None
) -> ResolvedRenderIntent:
    source = tmp_path / ("story.epub" if kind == "book" else "story.md")
    source.write_text("source", encoding="utf-8")
    profile = tmp_path / "cast.renderprofile.toml"
    profile.write_text("profile", encoding="utf-8")
    bundle_path = bundle or tmp_path / "story.ssmd-bundle"
    output_format = "m4b" if kind == "book" else "mp3"
    intent = (
        create_book_intent(source, profile=profile)
        if kind == "book"
        else create_document_intent(source, profile=profile)
    )
    return ResolvedRenderIntent(
        intent=intent,
        config_path=None,
        source=source,
        bundle=bundle_path,
        profile=profile,
        kind=kind,  # type: ignore[arg-type]
        chapters="all",
        language=None,
        output_format=output_format,
        output=tmp_path / f"story.{output_format}",
    )


class _FakeService:
    def __init__(self, resolved: ResolvedRenderIntent) -> None:
        self.resolved = resolved
        self.workspace = WorkspaceRef(resolved.bundle, resolved.kind)
        self.calls: list[tuple[str, Any]] = []
        self.resolve_options: dict[str, Any] = {}
        self.status_result = SimpleNamespace(compile=SimpleNamespace(state="complete"), variants=())
        self.failure: tuple[str, Exception] | None = None

    def resolve_intent(self, _intent: Any, **overrides: Any) -> ResolvedRenderIntent:
        self.resolve_options = overrides
        return self.resolved

    def resolve_workspace(self, _path: Any) -> WorkspaceRef:
        self.calls.append(("resolve", _path))
        return self.workspace

    def create_workspace(
        self, source: Any, request: Any, *, on_progress: Any = None
    ) -> WorkspaceRef:
        self.calls.append(("convert", request))
        return self.workspace

    def status(self, _workspace: Any, request: Any) -> Any:
        self.calls.append(("status", request))
        return self.status_result

    def plan(self, _workspace: Any, request: Any, *, on_progress: Any = None) -> Any:
        self.calls.append(("compile", request))
        return "compile-result"

    def render_plan(self, _workspace: Any, request: Any, *, on_progress: Any = None) -> Any:
        self.calls.append(("render-plan", request))
        return SimpleNamespace(native="projection", variant=VARIANT)

    def render(self, _workspace: Any, request: Any, *, on_progress: Any = None) -> Any:
        self.calls.append(("render", request))
        if self.failure is not None and self.failure[0] == "render":
            raise self.failure[1]
        return "render-result"

    def compose(self, _workspace: Any, request: Any, *, on_progress: Any = None) -> Any:
        self.calls.append(("compose", request))
        return "compose-result"

    def export(self, _workspace: Any, request: Any) -> Any:
        self.calls.append(("export", request))
        return "export-result"


def test_render_intent_orders_stages_and_preserves_exact_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = _resolved(tmp_path)
    service = _FakeService(resolved)
    monkeypatch.setattr("readio.render_service.preflight_export", lambda *_args, **_kwargs: {})

    result = render_intent(service, resolved.intent)

    assert [name for name, _ in service.calls] == [
        "convert",
        "compile",
        "render-plan",
        "render",
        "compose",
        "export",
    ]
    assert result.variant == VARIANT
    assert result.output == resolved.output
    assert result.converted is True
    assert result.pipeline.variant == VARIANT
    assert service.calls[3][1].variant == VARIANT
    assert service.calls[4][1].variant == VARIANT
    assert service.calls[5][1].variant == VARIANT
    assert service.calls[5][1].output == resolved.output


def test_render_intent_only_forces_explicit_stages_and_restarts_downstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = _resolved(tmp_path)
    service = _FakeService(resolved)
    monkeypatch.setattr("readio.render_service.preflight_export", lambda *_args, **_kwargs: {})

    render_intent(
        service,
        resolved.intent,
        RenderExecutionOptions(force_stages=("compile",), restart_from="render_plan"),
    )

    calls = dict(service.calls)
    assert calls["convert"].force is False
    assert calls["compile"].force is True
    assert calls["render-plan"].force is True
    assert calls["render"].force is True
    assert calls["compose"].force is True
    assert calls["export"].force is True
    assert service.resolve_options["force_output"] is True


def test_existing_bundle_is_reused_and_conversion_force_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "story.ssmd-bundle"
    bundle.mkdir()
    resolved = _resolved(tmp_path, bundle=bundle)
    service = _FakeService(resolved)
    events: list[Any] = []
    monkeypatch.setattr("readio.render_service.preflight_export", lambda *_args, **_kwargs: {})

    result = render_intent(service, resolved.intent, on_progress=events.append)
    assert result.converted is False
    assert [name for name, _ in service.calls] == [
        "resolve",
        "compile",
        "render-plan",
        "render",
        "compose",
        "export",
    ]
    assert events[0].stage == "convert"
    assert "reused validated bundle" in events[0].message

    forced_service = _FakeService(resolved)
    monkeypatch.setattr("readio.render_service.inspect_source_kind", lambda _source: "book")
    forced = render_intent(
        forced_service,
        resolved.intent,
        RenderExecutionOptions(force_stages=("convert",)),
    )
    assert forced.converted is True
    assert dict(forced_service.calls)["convert"].force is True


def test_forced_conversion_rejects_bundle_of_another_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "story.ssmd-bundle"
    bundle.mkdir()
    resolved = _resolved(tmp_path, bundle=bundle)
    service = _FakeService(resolved)
    monkeypatch.setattr("readio.render_service.inspect_source_kind", lambda _source: "document")
    monkeypatch.setattr("readio.render_service.preflight_export", lambda *_args, **_kwargs: {})

    with pytest.raises(ValueError, match="source kind does not match the intent"):
        render_intent(
            service,
            resolved.intent,
            RenderExecutionOptions(force_stages=("convert",)),
        )
    assert not service.calls


def test_plan_render_intent_reports_status_without_running_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "story.ssmd-bundle"
    bundle.mkdir()
    resolved = _resolved(tmp_path, bundle=bundle)
    service = _FakeService(resolved)
    monkeypatch.setattr("readio.render_service.preflight_export", lambda *_args, **_kwargs: {})

    plan = plan_render_intent(service, resolved.intent)

    assert plan.convert_required is False
    assert plan.output_exists is False
    assert plan.stages == ("convert", "compile", "render-plan", "render", "compose", "export")
    assert [name for name, _ in service.calls] == ["status"]
    assert service.calls[0][1].profile == resolved.profile


def test_stage_errors_include_package_profile_variant_and_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = _resolved(tmp_path)
    service = _FakeService(resolved)
    service.failure = (
        "render",
        ReadioStageError(
            "render",
            "voice engine failed",
            workspace=resolved.bundle,
            code="voice.failed",
            cause=RuntimeError("engine failure"),
        ),
    )
    monkeypatch.setattr("readio.render_service.preflight_export", lambda *_args, **_kwargs: {})

    with pytest.raises(IntentStageError) as captured:
        render_intent(service, resolved.intent)
    message = str(captured.value)
    assert "Package: voicerender" in message
    assert f"Profile: {resolved.profile}" in message
    assert f"Variant: {VARIANT}" in message
    assert "voice.failed" in message
    assert "--reset-stale-output" in message
