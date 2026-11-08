from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from readio.api import Readio, create_document_intent
from readio.api.types import RenderExecutionOptions, RenderRequest


def _request(tmp_path: Path) -> RenderRequest:
    return RenderRequest(
        source=tmp_path / "article.md",
        profile=tmp_path / "profile.toml",
        output=tmp_path / "article.mp3",
        format="mp3",
    )


def test_inspect_intent_uses_request_bridge_and_canonical_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intent = create_document_intent("article.md", profile="profile.toml")
    request = _request(tmp_path)
    plan = object()
    config_path = tmp_path / "settings.readio.toml"
    overrides = {"source": tmp_path / "override.md"}
    calls: dict[str, Any] = {}
    service = Readio()

    def build(received_intent: object, **received_overrides: Any) -> RenderRequest:
        calls["build"] = (received_intent, received_overrides)
        return request

    def inspect(received_request: RenderRequest) -> object:
        calls["inspect"] = received_request
        return plan

    monkeypatch.setattr(service, "request_from_intent", build)
    monkeypatch.setattr(service, "inspect", inspect)

    assert service.inspect_intent(intent, config_path=config_path, **overrides) is plan
    assert calls == {
        "build": (intent, {"config_path": config_path, **overrides}),
        "inspect": request,
    }


def test_plan_render_intent_maps_legacy_controls_to_canonical_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intent = create_document_intent("article.md", profile="profile.toml")
    request = _request(tmp_path)
    plan = object()
    ffmpeg = tmp_path / "ffmpeg"
    ffprobe = tmp_path / "ffprobe"
    options = RenderExecutionOptions(
        force_stages=("render",),
        restart_from="compile",
        reset_stale_output=True,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
    )
    service = Readio()
    inspected_requests: list[RenderRequest] = []

    def inspect(inspected: RenderRequest) -> object:
        inspected_requests.append(inspected)
        return plan

    monkeypatch.setattr(service, "request_from_intent", lambda *_args, **_kwargs: request)
    monkeypatch.setattr(service, "inspect", inspect)

    assert service.plan_render_intent(intent, options) is plan
    assert len(inspected_requests) == 1
    inspected = inspected_requests[0]
    assert inspected.rebuild_from == "compile"
    assert inspected.replace_output is True
    assert inspected.export.ffmpeg == ffmpeg
    assert inspected.export.ffprobe == ffprobe
    assert request.rebuild_from is None
    assert request.replace_output is False


def test_render_intent_delegates_to_render_with_dry_run_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intent = create_document_intent("article.md", profile="profile.toml")
    request = _request(tmp_path)
    result = object()
    progress = print
    calls: dict[str, Any] = {}
    service = Readio()

    def build(_intent: object, **_overrides: Any) -> RenderRequest:
        return request

    def render(received_request: RenderRequest, *, dry_run: bool, on_progress: object) -> object:
        calls.update(request=received_request, dry_run=dry_run, on_progress=on_progress)
        return result

    monkeypatch.setattr(service, "request_from_intent", build)
    monkeypatch.setattr(service, "render", render)

    assert service.render_intent(intent, dry_run=True, on_progress=progress) is result
    assert calls == {"request": request, "dry_run": True, "on_progress": progress}


def test_intent_execution_options_reject_unknown_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intent = create_document_intent("article.md", profile="profile.toml")
    request = _request(tmp_path)
    service = Readio()
    monkeypatch.setattr(service, "request_from_intent", lambda *_args, **_kwargs: request)

    with pytest.raises(ValueError, match="unknown forced stage"):
        service.plan_render_intent(
            intent,
            RenderExecutionOptions(force_stages=("unknown",)),  # type: ignore[arg-type]
        )
