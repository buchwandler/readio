# Python API

`readio.api` exposes a small orchestration service and management functions that delegate to specialist package-root APIs. Specialist results are returned unchanged; Readio does not mirror their option schemas or translate their validations.

```python
from pathlib import Path
from readio.api import (
    ComposeRequest,
    CreateWorkspaceRequest,
    ExportRequest,
    PlanRequest,
    Readio,
    RenderPlanRequest,
    RenderExecutionOptions,
    RenderIntentPlan,
    RenderIntentResult,
    ResolvedRenderIntent,
    create_book_intent,
    create_document_intent,
    load_render_intent,
    save_render_intent,
    RunRequest,
    BundleStatus,
    NextAction,
    StageStatus,
    StatusRequest,
    VariantStatus,
    VoiceRenderRequest,
)
```

## Bundle lifecycle

`Readio.create_workspace(source, request=None)` converts source material through SSMDConvert and returns an immutable `WorkspaceRef`. SSMDConvert chooses the bundle kind and default destination. Every resulting workspace uses the `.ssmd-bundle` suffix. `Readio.resolve_workspace(path)` asks SSMDConvert to validate and load an existing editable bundle.

```python
service = Readio()
workspace = service.create_workspace("article.txt")
assert workspace.root.name == "article.ssmd-bundle"
```

## Derived bundle status

`Readio.status(workspace, StatusRequest(...))` returns an in-memory, read-only view of specialist-owned state. It does not create stage records or modify the workspace. Stage states that a specialist cannot safely inspect are reported as `unknown`; incomplete work is not an exception.

```python
from readio.api import StatusRequest

status = service.status(
    "novel.ssmd-bundle",
    StatusRequest(chapters="5-17", profile=Path("narrator.renderprofile.toml")),
)
for action in status.next_actions:
    print(action.argv)  # exact argument array; no shell quoting required
```

`BundleStatus` contains compile state, per-variant `StageStatus` values, warnings, and `NextAction` argument arrays. Variant identifiers are preserved exactly. CLI verbose logging/timing is written to stderr and does not change API results.

Use `CreateWorkspaceRequest` to pass an explicit output path, language, chapter selector, or force behavior. It does not contain a kind selector; source and manifest kind detection belong to SSMDConvert.

## Intent-first complete renders

`RenderIntent` captures the requested book/document workflow; `VoiceIntent.profile` points to a separate, user-owned VoiceRender RenderProfile. It is not a voice-role or cast schema. Factories provide kind-specific defaults (book to M4B, document to MP3), while strict TOML helpers support persistent review:

```python
from pathlib import Path
from readio.api import (
    Readio,
    RenderExecutionOptions,
    create_document_intent,
    load_render_intent,
    save_render_intent,
)

config = Path("article.readio.toml")
intent = create_document_intent(
    Path("article.docx"),
    profile=Path("narrator.renderprofile.toml"),
    language="en-US",
)
save_render_intent(intent, config)
intent = load_render_intent(config)
service = Readio()
resolved = service.resolve_intent(intent, config_path=config)
plan = service.plan_render_intent(intent, config_path=config)
result = service.render_intent(intent, RenderExecutionOptions(), config_path=config)
print(resolved.output, plan.convert_required, result.variant)
```

`resolve_intent` applies config-relative paths and validates source/bundle kind, profile, AudioExport format/extension, and output safety. `plan_render_intent` reports existing specialist status and output collisions without executing pipeline stages. `render_intent` reuses a validated existing bundle by default, otherwise converts, compiles, projects, renders, composes, and exports. Its `RenderIntentResult` contains the resolved output, workspace, selected kind, exact projected variant, stage results, and conversion/timing summary.

`RenderExecutionOptions.force_stages` explicitly reruns selected stages; `restart_from` forces that stage and all downstream stages. `reset_stale_output`, `ffmpeg`, and `ffprobe` are independent execution controls. Existing output replacement is permitted only when export is forced. Export preflight uses AudioExport's format registry and doctor API before conversion. Specialist exceptions are wrapped with stage/package/profile/variant context while preserving their error code and cause.

## Planning and stage operations

`Readio.plan(workspace, PlanRequest(...))` delegates semantic compilation to UtterPlan. `Readio.render_plan(workspace, RenderPlanRequest(profile=...))` returns `RenderPlanResult(native, variant)`. The variant is the exact value returned by UtterPlan.

`Readio.render`, `Readio.compose`, and `Readio.export` forward requests to VoiceRender, AudioCompose, and AudioExport bundle APIs. Requests contain Readio routing fields and specialist-native option values; artifacts and reports remain package-owned. `WorkspaceRef.kind` is a consistency check, not a selector for kind-specific Readio implementations.

`ComposeRequest` supports bundle composition options, optional output policy, stage roots, pause policy, `save_audiojobs`, and force/fail-fast behavior. The specialist validates kind-sensitive composition rules.

Use one `ExportRequest` for both book and document bundles:

```python
export_request = ExportRequest(
    variant="exact-renderplan-variant",
    chapters="all",
    output=Path("article.m4a"),
    format="m4a",
    metadata={"title": "Article"},
)
result = service.export(workspace, export_request)
```

AudioExport owns kind-sensitive option validation. Its integration is optional; installing `readio[audioexport]` provides it.

## Lower-level complete pipeline sequencing

`Readio.run(source_or_bundle, RunRequest(...))` converts or resolves the input, then plans, projects, renders, composes, and optionally exports. It replaces downstream request variants with the exact projected variant before dispatch:

```python
profile = Path("narrator.renderprofile.toml")
result = service.run(
    "article.txt",
    RunRequest(
        create=CreateWorkspaceRequest(source=Path("article.txt")),
        plan=PlanRequest(language="en-US"),
        render_plan=RenderPlanRequest(profile=profile),
        render=VoiceRenderRequest(variant="pending"),
        compose=ComposeRequest(variant="pending"),
        export=ExportRequest(variant="pending", output=Path("article.m4a")),
    ),
)
print(result.workspace.root, result.variant)
```

`PipelineResult` retains the workspace, specialist results, projected variant, and optional export result. `PipelineEvent` adapts stage progress and retains the original event as `source`. Specialist failures are wrapped with stage context while preserving their error code and cause.

## Direct speech and management

`readio.api.speak(text, ...)` synthesizes literal text through VoiceRender and plays it locally unless `no_play=True`. It accepts a voice selector or a saved RenderProfile and role, and can save WAV output. It does not create a bundle or invoke UtterPlan, AudioCompose, or AudioExport.

`readio.api` also provides thin delegates for engine/model discovery and management, voice discovery and source/clone operations, selector parsing, and RenderProfile creation, validation, refresh, and role management. Calls use VoiceRender's public package-root API and preserve its result objects and validation behavior.

## ONNX Runtime controls

The runtime control functions delegate to VoiceRender's public package-root APIs and return its `OnnxRuntimeStatus` object for inspection:

```python
from readio.api import reset_runtime_providers, runtime_status, set_runtime_providers

status = runtime_status()
set_runtime_providers("cuda,cpu")
status = runtime_status()  # policy and resolution for future sessions
status = reset_runtime_providers()
```

`runtime_status()` describes the imported distribution/build, available providers, configured policy and its source, and resolution for new sessions; it does not report a provider active in an initialized synthesis session. A distribution/package is distinct from an execution provider. Environment variables such as `ONNXVOICE_PROVIDER` override the saved preference, and reset does not unset them. Explicit engine provider options and providers set in a RenderProfile remain intentional overrides. Readio does not install or switch runtime distributions.
