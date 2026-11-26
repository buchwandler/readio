# Readio architecture

Readio is a workflow gateway over specialist public APIs. A visible, user-owned `readio.render.v3` intent describes a complete request; the separate VoiceRender RenderProfile defines voice roles. SSMDConvert owns conversion and editable `.ssmd-bundle` workspaces, whose manifest determines whether content is a book or document. UtterPlan, VoiceRender, AudioCompose, and AudioExport own their stage contracts and perform kind-sensitive dispatch. File renders use temporary storage by default; a persistent bundle is always explicit.

## Public workflow API

`readio.api` is the public gateway for source and render workflows. `inspect_source()` returns normalized source metadata and chapter inventory without creating a workspace. `render_request_from_intent()` translates persisted user configuration into the canonical `RenderRequest`; it does not orchestrate stages itself. `Readio.inspect(RenderRequest)` and `Readio.render(RenderRequest)` are the shared read-only inspection and execution workflows. The `inspect_intent()` and `render_intent()` convenience methods delegate through that same request mapping and workflow rather than maintaining a second stage runner. Specialist stage request schemas remain owned by their packages.

## Intent-driven bundle workflow

The intent and profile live at user-selected paths, typically outside a persistent bundle. Readio resolves paths, validates configuration, and inspects specialist state before execution; temporary runs use a unique system temp directory and clean it on success or failure. Readio does not add workflow state to a workspace.

```text
source material
  → SSMDConvert bundle convert
  → <name>.ssmd-bundle/
      manifest kind: book | document
  → UtterPlan bundle compile
  → UtterPlan bundle render (profile-specific RenderPlan)
  → VoiceRender bundle render
  → AudioCompose bundle compose
  → AudioExport bundle export (optional)
```

`readio render` owns the complete workflow. With no explicit bundle it converts and processes inside temporary storage; `--bundle PATH` selects persistent storage, and a bundle supplied as `SOURCE` is reused as persistent input. Persistent book creation converts the full source inventory while the selected chapter scope controls downstream work. Readio preserves UtterPlan's exact projected variant through VoiceRender, AudioCompose, and AudioExport. `--rebuild-from` and `--replace-output` are separate explicit safety controls. Specialist CLIs remain available for deliberate stage-level debugging; Readio does not provide a parallel stage-runner command tree.

## Workflow progress

Readio forwards specialist progress synchronously through `WorkflowEvent` callbacks. Executed stages emit `stage.started`, specialist progress events, then `stage.completed`; reused stages emit only `stage.reused` and perform no specialist work. The progress adapter preserves determinate counters and specialist elapsed time when provided, while chapter keys are canonicalized in `details` only when chapter identity is available. Generic counters are not reinterpreted as chapter numbers.

AudioExport's public `export_bundle` API does not expose a progress callback. Readio therefore keeps export indeterminate and does not fabricate progress or ETA. TTSForge or another caller owns wall-clock timing and terminal presentation.
A book bundle may contain multiple chapter items; a document bundle represents a single document item. This is a content distinction recorded by the manifest, not a second workspace format or Readio routing policy.

## Ownership

| Concern                                                                                           | Owner                  | Readio's role                                                                                                           |
| ------------------------------------------------------------------------------------------------- | ---------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Requested render settings and intent file                                                         | User                   | Readio validates/resolves and orchestrates; no hidden workspace state                                                   |
| Voice roles and RenderProfile                                                                     | VoiceRender / user     | Readio references the profile; does not define a cast schema                                                            |
| Source conversion, `.ssmd-bundle`, manifest kind                                                  | SSMDConvert            | Forward conversion requests and resolve validated bundles                                                               |
| Semantic plans and RenderPlan projection                                                          | UtterPlan / RenderPlan | Forward bundle requests and preserve the exact returned variant                                                         |
| Profiles, voices, engines, and synthesis                                                          | VoiceRender            | Delegate management and bundle rendering                                                                                |
| Engine packages, model registries, model storage, revisions, downloads, verification, and removal | VoiceRender            | Normalize public model status and delegate explicit actions; no Python environment mutation or private cache inspection |
| ONNX Runtime build and provider policy                                                            | VoiceRender            | Expose status and provider selection; do not manage distributions                                                       |
| Composition and audio-job artifacts                                                               | AudioCompose           | Forward one bundle compose request and return the native result                                                         |
| Encoding and export artifacts                                                                     | AudioExport            | Forward one bundle export request; integration is optional                                                              |
| CLI, sequencing, and playback convenience                                                         | Readio                 | Coordinate public calls and adapt progress                                                                              |

`WorkspaceRef.kind` is an informational consistency check, not a selector for Readio-specific implementations. SSMDConvert determines source/bundle kind, specialists own chapter validation and kind-sensitive dispatch, and Readio rejects a resolved workspace whose kind contradicts the intent.

## Direct speech and management

`readio speak` is a separate literal-text path through VoiceRender and local playback; it does not create a bundle or invoke planning, composition, or export. Engine/model, voice/source, and RenderProfile operations delegate to VoiceRender's public management APIs. Backend engines are installed through VoiceRender extras.

## Engine model inventory and lifecycle

VoiceRender owns synthesis engine packages, model registries, model storage, revision semantics, downloads, verification, and removal. Readio normalizes public VoiceRender model records and delegates explicit model actions. Readio does not mutate Python package environments, inspect specialist-private caches, guess storage paths, or delete model directories.

The stable application contract is `readio.api.EngineModelInfo` with `Readio.list_models()`, `Readio.model_status()`, `Readio.install_model()`, `Readio.verify_model()`, and `Readio.remove_model()`. VoiceRender 0.2.12 does not publish byte counts, stable storage identity, per-model action capabilities, or authoritative update semantics; Readio leaves those values unknown or unasserted. `Readio.update_model()` reports unsupported operation rather than treating force-install as update. Existing module-level delegates preserve their VoiceRender-native results for compatibility.

## Manual specialist workflow

Use specialist CLIs directly when package-specific controls or detailed diagnostics are needed. Preserve the exact variant returned by UtterPlan:

```bash
ssmdconvert bundle convert novel.epub -o novel.ssmd-bundle
utterplan bundle compile novel.ssmd-bundle
voicerender profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o novel.ssmd-bundle/narrator.renderprofile.toml
utterplan bundle render novel.ssmd-bundle \
  --profile novel.ssmd-bundle/narrator.renderprofile.toml

VARIANT="<exact variant returned by utterplan bundle render>"
voicerender bundle render novel.ssmd-bundle --variant "$VARIANT"
audiocompose bundle compose novel.ssmd-bundle --variant "$VARIANT"
audioexport bundle export novel.ssmd-bundle --variant "$VARIANT" --output novel.m4b
```

Consult each specialist package for complete package-specific options.

## Persistent state

The editable bundle and all stage artifacts remain specialist-owned. Readio may read or create a user-selected `*.readio.toml` intent file, but never stores it as hidden project state. Readio writes no `.readio/` workspace directory, parallel manifest, stage status, RenderPlan, render manifest, composition job, export format, or status database. Its workflow plans, results, and progress events are in-memory orchestration values; inspection derives state read-only from specialist APIs.

## Dependency boundary

Readio imports specialist package-root public APIs only: SSMDConvert, UtterPlan, VoiceRender, AudioCompose, and AudioExport. It does not import specialist private submodules or backend SDKs. Playback and audio I/O helpers unrelated to bundle stages remain in Readio.

## Machine-level runtime policy

Readio exposes VoiceRender's ONNX Runtime status and provider-policy APIs through `readio.api` and `readio runtime`. The imported runtime distribution/build is not the same as an execution provider; provider selection affects future compatible sessions and does not prove which provider an existing session used. Environment variables such as `ONNXVOICE_PROVIDER` override a saved preference, and reset does not unset those variables. Explicit engine options and RenderProfile provider settings remain overrides. Provider aliases, precedence, validation, persistence, and installation guidance stay with VoiceRender; Readio does not install or switch runtime distributions or add runtime state to bundles, profiles, RenderPlans, or other project artifacts.

## Stable contracts and fail-closed execution

`Readio.inspect(RenderRequest)` is the canonical read-only planner. `status`, CLI dry-run, the Python API, and MCP all present the same `WorkflowPlan`. Stable reason codes, blockers, and logical artifact references describe plan state; human-readable reason strings and `details` remain diagnostic only. Replacement requires explicit rebuild authorization and specialist-certified safety. Existing final output replacement has its own explicit control.

One public serializer defines the versioned JSON projections for plans, render results, errors, capabilities, and synthesized speech. Intent schema `readio.render.v3` is distinct from response schemas such as `readio.workflow-plan.v1`, `readio.render-result.v1`, and `readio.error-info.v1`. Capability discovery is read-only. Cancellation is cooperative at supported boundaries, and AudioExport cannot currently be interrupted through its public API. Optional execution reports contain in-memory stage timing and version metadata only.

## Optional MCP adapter

The optional MCP server is an adapter over `readio.api`, not a second orchestrator. It imports no specialist packages directly and runs over stdio after installing `readio[mcp]`. Inspection and capability tools are read-only. `speech_render` requires explicit source, profile, and output paths, inspects the request first, returns a blocked plan without mutation, and only delegates execution to `Readio.render()` when the plan is executable. Rebuild and final-output replacement remain separate explicit controls. `speech_speak` returns in-memory WAV data as base64 and does not play audio. No database, hidden project path, queue, daemon, or audiobook library is introduced.
