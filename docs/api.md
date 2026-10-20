# Python API

`readio.api` exposes a small orchestration service and management functions that delegate to specialist package-root APIs. Specialist results are returned unchanged; Readio does not mirror their option schemas or translate their exceptions.

```python
from pathlib import Path
from readio.api import (
    BookExportRequest,
    CreateWorkspaceRequest,
    ComposeRequest,
    PlanRequest,
    Readio,
    RenderPlanRequest,
    RunRequest,
    VoiceRenderRequest,
)
```

## Workspace lifecycle

`Readio.create_workspace(source, request=None)` converts source material through SSMDConvert and returns an immutable `WorkspaceRef`. EPUB input defaults to a `.ssmdbook`; other supported input defaults to `.ssmdoc`. `Readio.resolve_workspace(path)` validates and loads an existing native workspace. No Readio project registry or migration is involved.

```python
service = Readio()
workspace = service.create_workspace("article.txt")
assert workspace.root.suffix == ".ssmdoc"
```

Use `CreateWorkspaceRequest` to select kind, output path, language, chapter range, or force behavior. Its source must match the `source` argument.

## Planning and pipeline sequencing

`Readio.plan(workspace, PlanRequest(...))` delegates semantic compilation to UtterPlan. `Readio.render_plan(workspace, RenderPlanRequest(profile=...))` returns `RenderPlanResult(native, variant)`. The `variant` is the exact value returned by UtterPlan.

`Readio.render`, `Readio.compose`, and `Readio.export` dispatch by workspace kind to VoiceRender, AudioCompose, and AudioExport. Requests contain Readio routing fields and specialist-native option values; artifacts and reports remain package-owned.

`Readio.run(source_or_workspace, RunRequest(...))` performs workspace creation/resolution, planning, projection, rendering, composition, and optional export. It replaces downstream request variants with the exact projected variant before dispatch:

```python
profile = Path("narrator.renderprofile.toml")
result = service.run(
    workspace.root,
    RunRequest(
        plan=PlanRequest(language="en-US"),
        render_plan=RenderPlanRequest(profile=profile),
        render=VoiceRenderRequest(variant="pending"),
        compose=ComposeRequest(variant="pending"),
    ),
)
print(result.variant)  # exact UtterPlan variant
```

`PipelineResult` retains the workspace, specialist results, projected variant, and optional export result. `PipelineEvent` adapts stage progress and retains the original event as `source`. Specialist exceptions propagate through a small stage-context wrapper.

## Book operations

`Readio.create_workspace()` converts EPUB input into a native `.ssmdbook`; `CreateWorkspaceRequest` can select chapters and conversion language. `PlanRequest`, `RenderPlanRequest`, `VoiceRenderRequest`, and `ComposeRequest` carry book chapter selectors to the corresponding specialists. Use `BookExportRequest` when requesting book export through the Python service.

Store a book's RenderProfile inside its workspace so UtterPlan can record a workspace-relative provenance reference during projection.

## Direct speech

`readio.api.speak(text, ...)` synthesizes literal text through VoiceRender and plays it locally unless `no_play=True`. It accepts a voice selector or a saved RenderProfile and role, and can save WAV output. It does not create a workspace or invoke UtterPlan, AudioCompose, or AudioExport.

## VoiceRender management

`readio.api` provides thin delegates for engine/model discovery and management, voice discovery and source/clone operations, selector parsing, and RenderProfile creation, validation, refresh, and role management. Calls use VoiceRender's public package-root API and preserve its result objects and validation behavior. Readio does not provide voice design or an engine implementation.

## Compatibility policy

Version 0.5 is a clean break. Readio-owned project directories, settings, plans, manifests, and old stage APIs are unsupported. There is no migration, compatibility alias, or hidden state attachment. Use SSMDConvert to create native `.ssmdoc`/`.ssmdbook` workspaces from source material, or use the specialist packages directly.
