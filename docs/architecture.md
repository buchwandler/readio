# Readio architecture

Readio is an intent-first orchestration layer over specialist public APIs. A visible, user-owned TOML render intent describes a complete request; the separate VoiceRender RenderProfile defines voice roles. SSMDConvert owns conversion and editable `.ssmd-bundle` workspaces, whose manifest determines whether content is a book or document. UtterPlan, VoiceRender, AudioCompose, and optional AudioExport own their stage contracts and perform kind-sensitive dispatch.

## Intent-driven bundle workflow

The intent and profile live at user-selected paths, typically outside the bundle. Readio resolves paths, validates configuration, and checks selected AudioExport/FFmpeg/FFprobe readiness before launching conversion; it does not add workflow state to the workspace.

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

`readio render` resolves the saved intent (or one-off source options), then sequences these stages; `readio bundle run` remains available for lower-level control. The RenderPlan result supplies the exact variant sent unchanged to VoiceRender, AudioCompose, and optional AudioExport. Explicit force/restart controls protect existing artifacts, and AudioExport preflight runs before conversion. Readio does not normalize or reconstruct the variant.

A book bundle may contain multiple chapter items; a document bundle represents a single document item. This is a content distinction recorded by the manifest, not a second workspace format or Readio routing policy.

## Ownership

| Concern                                          | Owner                  | Readio's role                                                         |
| ------------------------------------------------ | ---------------------- | --------------------------------------------------------------------- |
| Requested render settings and intent file        | User                   | Readio validates/resolves and orchestrates; no hidden workspace state |
| Voice roles and RenderProfile                    | VoiceRender / user     | Readio references the profile; does not define a cast schema          |
| Source conversion, `.ssmd-bundle`, manifest kind | SSMDConvert            | Forward conversion requests and resolve validated bundles             |
| Semantic plans and RenderPlan projection         | UtterPlan / RenderPlan | Forward bundle requests and preserve the exact returned variant       |
| Profiles, voices, engines, models, and synthesis | VoiceRender            | Delegate management and bundle rendering                              |
| ONNX Runtime build and provider policy           | VoiceRender            | Expose status and provider selection; do not manage distributions     |
| Composition and audio-job artifacts              | AudioCompose           | Forward one bundle compose request and return the native result       |
| Encoding and export artifacts                    | AudioExport            | Forward one bundle export request; integration is optional            |
| CLI, sequencing, and playback convenience        | Readio                 | Coordinate public calls and adapt progress                            |

`WorkspaceRef.kind` is an informational consistency check, not a selector for Readio-specific implementations. SSMDConvert determines source/bundle kind, specialists own chapter validation and kind-sensitive dispatch, and Readio rejects a resolved workspace whose kind contradicts the intent.

## Direct speech and management

`readio speak` is a separate literal-text path through VoiceRender and local playback; it does not create a bundle or invoke planning, composition, or export. Engine/model, voice/source, and RenderProfile operations delegate to VoiceRender's public management APIs. Backend engines are installed through VoiceRender extras.

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

The editable bundle and all stage artifacts remain specialist-owned. Readio may read or create a user-selected `*.readio.toml` intent file, but never stores it as hidden project state. Readio writes no `.readio/` workspace directory, parallel manifest, stage status, RenderPlan, render manifest, composition job, export format, or status database. Its `WorkspaceRef`, progress events, pipeline results, and status view are in-memory routing/adaptation values; status is derived read-only from specialist APIs.

## Dependency boundary

Readio imports specialist package-root public APIs only: SSMDConvert, UtterPlan, VoiceRender, AudioCompose, and optional AudioExport. It does not import specialist private submodules or backend SDKs. Playback and audio I/O helpers unrelated to bundle stages remain in Readio.

## Machine-level runtime policy

Readio exposes VoiceRender's ONNX Runtime status and provider-policy APIs through `readio.api` and `readio runtime`. The imported runtime distribution/build is not the same as an execution provider; provider selection affects future compatible sessions and does not prove which provider an existing session used. Environment variables such as `ONNXVOICE_PROVIDER` override a saved preference, and reset does not unset those variables. Explicit engine options and RenderProfile provider settings remain overrides. Provider aliases, precedence, validation, persistence, and installation guidance stay with VoiceRender; Readio does not install or switch runtime distributions or add runtime state to bundles, profiles, RenderPlans, or other project artifacts.
