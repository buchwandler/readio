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

## Planning and stage operations

`Readio.plan(workspace, PlanRequest(...))` delegates semantic compilation to UtterPlan. `Readio.render_plan(workspace, RenderPlanRequest(profile=...))` returns `RenderPlanResult(native, variant)`. The variant is the exact value returned by UtterPlan.

`Readio.render`, `Readio.compose`, and `Readio.export` forward requests to VoiceRender, AudioCompose, and AudioExport bundle APIs. Requests contain Readio routing fields and specialist-native option values; artifacts and reports remain package-owned. `WorkspaceRef.kind` is informational and does not select a Readio implementation.

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

## Complete pipeline sequencing

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
