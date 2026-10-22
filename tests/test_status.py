from __future__ import annotations

import json
import shlex
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from readio import cli
from readio import status as status_module
from readio.api import (
    BundleStatus,
    NextAction,
    StageStatus,
    StatusRequest,
    WorkspaceRef,
)


def _install_provider_fakes(monkeypatch: pytest.MonkeyPatch, variants: tuple[Any, ...]) -> None:
    monkeypatch.setattr(
        status_module.providers,
        "inspect_bundle",
        lambda workspace: SimpleNamespace(kind="book"),
    )
    monkeypatch.setattr(
        status_module.providers,
        "inspect_compile",
        lambda workspace, chapters: SimpleNamespace(
            state="complete", summary="2/2 chapters compiled", current=2, total=2
        ),
    )
    monkeypatch.setattr(
        status_module.providers,
        "inspect_variants",
        lambda workspace: SimpleNamespace(variants=variants),
    )
    monkeypatch.setattr(status_module.providers, "audioexport_installed", lambda: False)


def test_compile_missing_recommends_only_compile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        status_module.providers,
        "inspect_bundle",
        lambda workspace: SimpleNamespace(kind="book"),
    )
    monkeypatch.setattr(
        status_module.providers,
        "inspect_compile",
        lambda workspace, chapters: SimpleNamespace(
            state="not_started", summary="No compile output."
        ),
    )
    monkeypatch.setattr(
        status_module.providers,
        "inspect_variants",
        lambda workspace: SimpleNamespace(variants=()),
    )

    result = status_module.inspect_status(tmp_path / "story.ssmd-bundle")

    assert result.compile.state == "not_started"
    assert [action.stage for action in result.next_actions] == ["compile"]
    assert result.next_actions[0].argv[:3] == ("readio", "bundle", "compile")


def test_missing_inspector_is_unknown_not_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        status_module.providers,
        "inspect_bundle",
        lambda workspace: SimpleNamespace(kind="document"),
    )
    monkeypatch.setattr(
        status_module.providers, "inspect_compile", lambda workspace, chapters: None
    )
    monkeypatch.setattr(
        status_module.providers,
        "inspect_variants",
        lambda workspace: SimpleNamespace(variants=()),
    )

    result = status_module.inspect_status(tmp_path / "book.ssmd-bundle")

    assert result.compile.state == "unknown"
    assert result.variants == ()
    assert result.next_actions == ()


def test_exact_variant_filter_and_partial_render_next_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exact = "Voice / test--v.2"
    variants = (
        SimpleNamespace(
            name=exact,
            profile_id="profile-id",
            plan_status="valid",
            validation_error=None,
            state="partial",
            render_status="partial",
            completed_units=3,
            total_units=5,
            available_speech=8,
            error_code=None,
            error_message=None,
        ),
        SimpleNamespace(
            name="other-variant",
            plan_status="valid",
            state="complete",
            render_status="complete",
            completed_units=5,
            total_units=5,
        ),
    )
    _install_provider_fakes(monkeypatch, variants)
    monkeypatch.setattr(
        status_module.providers,
        "inspect_compose",
        lambda *args: pytest.fail("compose is blocked while render is partial"),
    )

    result = status_module.inspect_status(
        tmp_path / "book.ssmd-bundle", StatusRequest(variant=exact)
    )

    assert [variant.variant for variant in result.variants] == [exact]
    assert result.variants[0].render.state == "partial"
    assert result.variants[0].render.current == 3
    assert result.variants[0].compose.state == "blocked"
    assert result.variants[0].export.state == "unavailable"
    assert result.next_actions[0].argv[5] == exact


def test_compose_complete_recommends_optional_export_with_required_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    variant = SimpleNamespace(
        name="narration--profile",
        profile_id="profile-id",
        plan_status="valid",
        state="complete",
        render_status="complete",
        completed_units=1,
        total_units=1,
    )
    _install_provider_fakes(monkeypatch, (variant,))
    monkeypatch.setattr(
        status_module.providers,
        "inspect_compose",
        lambda workspace, name, chapters: SimpleNamespace(
            state="complete", summary="1/1 chapters composed", current=1, total=1
        ),
    )
    monkeypatch.setattr(
        status_module.providers,
        "inspect_export",
        lambda *args: SimpleNamespace(state="not_started", summary="No export yet."),
    )
    monkeypatch.setattr(status_module.providers, "audioexport_installed", lambda: True)

    result = status_module.inspect_status(tmp_path / "book.ssmd-bundle")

    assert result.variants[0].compose.state == "complete"
    assert result.variants[0].export.state == "not_started"
    assert result.next_actions[0].stage == "export"
    assert result.next_actions[0].argv == ()
    assert result.next_actions[0].requires == ("output",)


def test_inspection_errors_are_stage_local_and_status_does_not_create_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "book.ssmd-bundle"
    root.mkdir()
    before = sorted(path.relative_to(root) for path in root.rglob("*"))
    monkeypatch.setattr(
        status_module.providers,
        "inspect_bundle",
        lambda workspace: SimpleNamespace(kind="book"),
    )
    monkeypatch.setattr(
        status_module.providers,
        "inspect_compile",
        lambda workspace, chapters: SimpleNamespace(state="complete", summary="compiled"),
    )
    monkeypatch.setattr(
        status_module.providers,
        "inspect_variants",
        lambda workspace: SimpleNamespace(variants=()),
    )

    result = status_module.inspect_status(root)

    assert result.compile.state == "complete"
    assert sorted(path.relative_to(root) for path in root.rglob("*")) == before
    assert result.next_actions[0].requires == ("profile",)


def _cli_status_result(root: Path) -> BundleStatus:
    action = NextAction(
        stage="compile",
        reason="No compile output exists.",
        argv=("readio", "bundle", "compile", str(root), "--chapters", "5-17"),
    )
    return BundleStatus(
        workspace=WorkspaceRef(root, "book"),
        compile=StageStatus("compile", "not_started", "No compile output exists."),
        variants=(),
        next_actions=(action,),
    )


def test_status_cli_json_preserves_request_and_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "story.ssmd-bundle"
    exact_variant = "Narrator / test--v.2"
    captured: dict[str, Any] = {}
    result = _cli_status_result(root)

    class FakeReadio:
        def status(self, workspace: Path, request: StatusRequest) -> BundleStatus:
            captured["workspace"] = workspace
            captured["request"] = request
            return result

    monkeypatch.setattr(cli.api, "Readio", FakeReadio)
    exit_code = cli.main(
        [
            "bundle",
            "status",
            str(root),
            "--profile",
            "narrator.renderprofile.toml",
            "--variant",
            exact_variant,
            "--chapters",
            "5-17",
            "-vv",
            "--json",
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["result"]["compile"]["state"] == "not_started"
    assert payload["result"]["next_actions"][0]["argv"][-1] == "5-17"
    assert captured["request"].variant == exact_variant
    assert captured["request"].chapters == "5-17"
    assert captured["request"].profile == Path("narrator.renderprofile.toml")
    assert "command.end" in output.err
    assert "elapsed_ms=" in output.err


def test_status_cli_human_output_shell_quotes_next_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "story title.ssmd-bundle"
    result = _cli_status_result(root)

    class FakeReadio:
        def status(self, workspace: Path, request: StatusRequest) -> BundleStatus:
            return result

    monkeypatch.setattr(cli.api, "Readio", FakeReadio)
    assert cli.main(["bundle", "status", str(root), "--chapters", "5-17", "-v"]) == 0
    output = capsys.readouterr()
    assert "Kind: book" in output.out
    assert shlex.join(result.next_actions[0].argv) in output.out
    assert "elapsed_ms=" in output.err


def test_render_not_started_recommends_render_without_composition_inspection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    variant = SimpleNamespace(
        name="voice--variant",
        plan_status="valid",
        state="not_started",
        render_status="not_rendered",
        completed_units=0,
        total_units=2,
    )
    _install_provider_fakes(monkeypatch, (variant,))
    monkeypatch.setattr(
        status_module.providers,
        "inspect_compose",
        lambda *args: pytest.fail("composition inspection must wait for a complete render"),
    )

    result = status_module.inspect_status(tmp_path / "story.ssmd-bundle")

    assert result.variants[0].render.state == "not_started"
    assert result.variants[0].compose.state == "blocked"
    assert result.next_actions[0].stage == "render"
    assert result.next_actions[0].argv[5] == "voice--variant"


def test_stale_compile_suppresses_all_downstream_actions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_provider_fakes(monkeypatch, ())
    monkeypatch.setattr(
        status_module.providers,
        "inspect_compile",
        lambda *_args: SimpleNamespace(state="stale", summary="Compile output is stale."),
    )
    monkeypatch.setattr(
        status_module.providers,
        "inspect_compose",
        lambda *args: pytest.fail("stale compile must not suggest composition"),
    )

    result = status_module.inspect_status(tmp_path / "story.ssmd-bundle")

    assert result.compile.state == "stale"
    assert [action.stage for action in result.next_actions] == ["compile"]


def test_stale_render_recommends_only_destructive_render_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    variant = SimpleNamespace(
        name="stale--render",
        plan_status="valid",
        state="stale",
        render_status="stale",
        details={"recommended_flags": ("--reset-stale-output",), "destructive": True},
    )
    _install_provider_fakes(monkeypatch, (variant,))
    monkeypatch.setattr(
        status_module.providers,
        "inspect_compose",
        lambda *args: pytest.fail("stale render must not suggest composition"),
    )

    result = status_module.inspect_status(tmp_path / "story.ssmd-bundle")

    assert result.variants[0].render.state == "stale"
    assert len(result.next_actions) == 1
    assert result.next_actions[0].stage == "render"
    assert result.next_actions[0].destructive is True
    assert "--reset-stale-output" in result.next_actions[0].argv


def test_render_complete_with_missing_compose_recommends_compose(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    variant = SimpleNamespace(
        name="ready--variant",
        plan_status="valid",
        state="complete",
        render_status="complete",
    )
    _install_provider_fakes(monkeypatch, (variant,))
    monkeypatch.setattr(
        status_module.providers,
        "inspect_compose",
        lambda *_args: SimpleNamespace(state="not_started", summary="No composition output."),
    )

    result = status_module.inspect_status(tmp_path / "story.ssmd-bundle")

    assert result.variants[0].compose.state == "not_started"
    assert result.variants[0].export.state == "unavailable"
    assert result.next_actions[0].stage == "compose"


def test_unfiltered_status_keeps_each_exact_variant_independent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first_name = "Narrator / first--v.1"
    second_name = "Narrator second--v.2"
    variants = (
        SimpleNamespace(
            name=first_name,
            plan_status="valid",
            state="complete",
            render_status="complete",
        ),
        SimpleNamespace(
            name=second_name,
            plan_status="valid",
            state="partial",
            render_status="partial",
        ),
    )
    _install_provider_fakes(monkeypatch, variants)
    monkeypatch.setattr(
        status_module.providers,
        "inspect_compose",
        lambda *_args: SimpleNamespace(state="not_started", summary="No composition output."),
    )

    result = status_module.inspect_status(tmp_path / "story.ssmd-bundle")

    assert [item.variant for item in result.variants] == [first_name, second_name]
    assert [action.stage for action in result.next_actions] == ["compose", "render"]
    assert result.next_actions[0].argv[5] == first_name
    assert result.next_actions[1].argv[5] == second_name


def test_compile_inspector_errors_are_reported_without_failing_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        status_module.providers,
        "inspect_bundle",
        lambda *_args: SimpleNamespace(kind="document"),
    )
    monkeypatch.setattr(
        status_module.providers,
        "inspect_compile",
        lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
    )
    monkeypatch.setattr(
        status_module.providers,
        "inspect_variants",
        lambda *_args: SimpleNamespace(variants=()),
    )

    result = status_module.inspect_status(tmp_path / "article.ssmd-bundle")

    assert result.compile.state == "error"
    assert result.compile.details["error_type"] == "PermissionError"
    assert result.warnings == ("UtterPlan inspection failed: denied",)
