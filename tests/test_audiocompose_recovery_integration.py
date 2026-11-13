from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from renderplan import (
    PauseItem,
    Producer,
    RenderManifest,
    RenderPlan,
    RenderPlanSet,
    RenderPlanSetChapter,
    RenderProfile,
    RoleProfile,
    save_manifest,
    save_plan,
    save_profile,
    save_renderplan_set,
)

from readio import render_workflow as workflow
from readio.api.types import RenderRequest
from readio.integrations.audiocompose import inspect_bundle_composition
from readio.workspace import WorkspacePolicy, WorkspaceRef

_LEGACY_ERROR = (
    "VoiceRender artifacts use an unsupported audio artifact policy; "
    "re-render with VoiceRender >=0.2.11 before composition"
)
_POLICY = "voicerender.preserve-silence.v3"


def _build_historical_bundle(
    root: Path,
) -> tuple[str, dict[str, tuple[RenderManifest, Path, Path]]]:
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps({"format": "ssmdconvert.book", "schema_version": 1}),
        encoding="utf-8",
    )
    profile = RenderProfile(
        roles={"narrator": RoleProfile(language="en-US", binding={"renderer": "fixture"})},
        default_role="narrator",
    ).with_identity()
    profile_id = profile.profile_id or profile.computed_id()
    fingerprint = profile_id.removeprefix("sha256:")[:12]
    label = "narrator--pt-engaging-v1"
    variant = f"{label}--{fingerprint}"
    variant_dir = root / "renderplan" / variant
    variant_dir.mkdir(parents=True)
    save_profile(profile, variant_dir / "renderprofile.toml")

    chapters: list[RenderPlanSetChapter] = []
    artifacts: dict[str, tuple[RenderManifest, Path, Path]] = {}
    for source_number in range(5, 18):
        unit_id = f"chapter-{source_number:04d}"
        plan = RenderPlan(
            source_plan_id=f"sha256:{source_number:064x}",
            render_profile_id=profile_id,
            flow=(PauseItem(duration_ms=0, reason="leading").with_identity(),),
        ).with_identity()
        plan_name = f"{unit_id}.renderplan.toml"
        save_plan(plan, variant_dir / plan_name)
        chapters.append(
            RenderPlanSetChapter(
                id=unit_id,
                source_number=source_number,
                source=f"../../chapters/{unit_id}.md",
                utterplan=f"../../utterplan/{unit_id}.toml",
                source_plan_id=plan.source_plan_id,
                renderplan=plan_name,
                render_plan_id=plan.plan_id or plan.computed_id(),
            )
        )

        render_dir = root / "voicerender" / variant / unit_id
        render_dir.mkdir(parents=True)
        manifest_path = render_dir / "render-manifest.toml"
        diagnostics_path = render_dir / "render-diagnostics.json"
        manifest = RenderManifest(
            render_plan_id=plan.plan_id or plan.computed_id(),
            speech=(),
            complete=True,
            producer=Producer(name="voicerender", version="0.2.11"),
        ).with_identity()
        save_manifest(manifest, manifest_path)
        diagnostics = {
            "schema": "voicerender.render-diagnostics.v1",
            "audio_artifact_policy": "voicerender.raw-synthesis.v2",
            "render_plan_id": plan.plan_id or plan.computed_id(),
            "emphasis_policy": "reject",
            "pending_composition": False,
            "deferred_controls": [],
        }
        diagnostics_path.write_text(json.dumps(diagnostics), encoding="utf-8")
        artifacts[unit_id] = (manifest, manifest_path, diagnostics_path)

    save_renderplan_set(
        variant_dir / "renderplan-set.toml",
        RenderPlanSet(
            variant=label,
            profile_fingerprint=fingerprint,
            producer_version="0.2.0",
            manifest="../../manifest.json",
            utterplan_dir="../../utterplan",
            profile_id=profile_id,
            snapshot="renderprofile.toml",
            source_profile="../../narration.renderprofile.toml",
            chapters=tuple(chapters),
        ),
    )

    output_dir = root / "audiocompose" / variant
    output_dir.mkdir(parents=True)
    report = [
        'format = "audiocompose-book-report"',
        "version = 1",
        f"variant = {json.dumps(variant)}",
        f"render_profile_id = {json.dumps(profile_id)}",
        'audiocompose_version = "0.3.11.dev0+fixture"',
        'emphasis_policy = "segment_fallback"',
        'pause_policy_provenance = "renderprofile"',
        "requested = 13",
        "written = 0",
        "reused = 0",
        "failed = 13",
        "skipped = 0",
    ]
    for source_number in range(5, 18):
        unit_id = f"chapter-{source_number:04d}"
        report.extend(
            [
                "",
                "[[item]]",
                f"id = {json.dumps(unit_id)}",
                f"source_number = {source_number}",
                f"render_plan_id = {json.dumps(artifacts[unit_id][0].render_plan_id)}",
                'status = "failed"',
                'error_code = "handoff.invalid"',
                f"error_message = {json.dumps(_LEGACY_ERROR)}",
                'failure_stage = "handoff"',
            ]
        )
    (output_dir / "compose-report.toml").write_text("\n".join(report) + "\n", encoding="utf-8")
    return variant, artifacts


def _repair_voice_render(artifacts: dict[str, tuple[RenderManifest, Path, Path]]) -> None:
    for manifest, manifest_path, diagnostics_path in artifacts.values():
        repaired = replace(
            manifest,
            producer=Producer(name="voicerender", version="0.2.12"),
            manifest_id=None,
        ).with_identity()
        save_manifest(repaired, manifest_path)
        diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
        diagnostics["audio_artifact_policy"] = _POLICY
        diagnostics_path.write_text(json.dumps(diagnostics), encoding="utf-8")


def _write_successful_composition(
    root: Path,
    variant: str,
    artifacts: dict[str, tuple[RenderManifest, Path, Path]],
) -> None:
    output_dir = root / "audiocompose" / variant
    items: list[str] = []
    for unit_id, (_old_manifest, manifest_path, diagnostics_path) in artifacts.items():
        manifest = _load_render_manifest(manifest_path)
        wav_name = f"{unit_id}.wav"
        (output_dir / wav_name).write_bytes(b"test audio")
        items.extend(
            [
                "",
                "[[item]]",
                f"id = {json.dumps(unit_id)}",
                f"source_number = {int(unit_id[-4:])}",
                f"source_plan_id = {json.dumps(f'sha256:{int(unit_id[-4:]):064x}')}",
                f"render_profile_id = {json.dumps(_render_profile_id(root, variant))}",
                f"render_plan_id = {json.dumps(manifest.render_plan_id)}",
                f"manifest_id = {json.dumps(manifest.manifest_id or manifest.computed_id())}",
                f'manifest_sha256 = "sha256:{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}"',
                f'render_diagnostics_sha256 = "sha256:{hashlib.sha256(diagnostics_path.read_bytes()).hexdigest()}"',
                f"output = {json.dumps(wav_name)}",
                'status = "written"',
            ]
        )
    report = [
        'format = "audiocompose-book-report"',
        "version = 1",
        f"variant = {json.dumps(variant)}",
        f"render_profile_id = {json.dumps(_render_profile_id(root, variant))}",
        "requested = 13",
        "written = 13",
        "reused = 0",
        "failed = 0",
        "skipped = 0",
        *items,
    ]
    (output_dir / "compose-report.toml").write_text("\n".join(report) + "\n", encoding="utf-8")


def _load_render_manifest(path: Path) -> RenderManifest:
    from renderplan import load_manifest

    return load_manifest(path)


def _render_profile_id(root: Path, variant: str) -> str:
    from renderplan import load_profile

    return load_profile(root / "renderplan" / variant / "renderprofile.toml").profile_id


def test_real_audiocompose_recovery_converges_through_rerender_recompose_and_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "historical.ssmd-bundle"
    variant, artifacts = _build_historical_bundle(bundle)
    source = tmp_path / "book.epub"
    source.write_bytes(b"fixture source")
    profile_path = tmp_path / "narrator.renderprofile.toml"
    profile_path.write_text("fixture profile", encoding="utf-8")
    output = tmp_path / "book.m4b"
    state = {"rendered": False, "composed": False, "exported": False}
    workspace = WorkspaceRef(root=bundle, kind="book")

    policy = WorkspacePolicy(
        mode="persistent",
        root=bundle,
        source_is_bundle=False,
        newly_created=False,
        cleanup=False,
    )
    monkeypatch.setattr(workflow, "resolve_workspace_policy", lambda *_args, **_kwargs: policy)
    monkeypatch.setattr(workflow, "resolve_workspace", lambda _root: workspace)
    monkeypatch.setattr(workflow, "inspect_source", lambda _source: SimpleNamespace(kind="book"))
    monkeypatch.setattr(workflow, "load_render_profile", lambda _profile: object())
    monkeypatch.setattr(
        workflow, "supported_formats", lambda: {"m4b": SimpleNamespace(extension=".m4b")}
    )
    monkeypatch.setattr(
        workflow,
        "inspect_source_provenance",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="match", replacement_safe=True, force_required=False
        ),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_compile",
        lambda *_args, **_kwargs: SimpleNamespace(state="complete", replacement_safe=True),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_projection",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="complete", variant=variant, replacement_safe=True
        ),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_render",
        lambda *_args, **_kwargs: SimpleNamespace(state="complete", replacement_safe=True),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_export",
        lambda *_args, **_kwargs: SimpleNamespace(
            state=("complete" if state["exported"] else "not_started")
            if state["composed"]
            else "error",
            reason="Composition has not converged.",
        ),
    )

    initial_composition = inspect_bundle_composition(workspace, variant=variant, chapters="5-17")
    assert initial_composition.state == "error"
    assert initial_composition.recovery_action == "rerender"
    assert initial_composition.automatic_convergence_safe is True
    assert len(initial_composition.selected) == 13

    request = RenderRequest(
        source=source,
        profile=profile_path,
        bundle=bundle,
        chapters="5-17",
        variant=variant,
        output=output,
    )
    initial_plan = workflow.inspect_render_workflow(request)
    initial_steps = {step.stage: step for step in initial_plan.steps}
    assert initial_steps["render"].action == "replace"
    assert initial_steps["compose"].action == "execute"

    post_render_plans = []
    post_render_composition = []
    calls: list[tuple[str, object]] = []

    class StatefulService:
        def render(self, _workspace: object, render_request: object) -> None:
            calls.append(("render", render_request))
            _repair_voice_render(artifacts)
            state["rendered"] = True
            post_render_composition.append(
                inspect_bundle_composition(workspace, variant=variant, chapters="5-17")
            )
            post_render_plans.append(workflow.inspect_render_workflow(request))

        def compose(self, _workspace: object, compose_request: object) -> None:
            calls.append(("compose", compose_request))
            _write_successful_composition(bundle, variant, artifacts)
            state["composed"] = True

        def export(self, _workspace: object, export_request: object) -> None:
            calls.append(("export", export_request))
            state["exported"] = True

    result = workflow.render_workflow(StatefulService(), request)

    assert post_render_composition[0].state == "stale"
    assert post_render_composition[0].recovery_action == "recompose"
    assert post_render_composition[0].automatic_convergence_safe is True
    post_render_steps = {step.stage: step for step in post_render_plans[0].steps}
    assert post_render_steps["render"].action == "reuse"
    assert post_render_steps["compose"].action == "replace"
    assert post_render_steps["export"].action == "execute"
    assert [stage for stage, _request in calls] == ["render", "compose", "export"]
    assert calls[0][1].force is True
    assert calls[0][1].reset_stale_output is True
    assert calls[1][1].force is True
    assert result.executed_stages == ("render", "compose", "export")
