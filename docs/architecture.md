# Readio architecture

Readio is a thin orchestration layer over specialist public APIs. SSMDConvert owns conversion and editable `.ssmd-bundle` workspaces; the bundle manifest records whether content is a book or a document. UtterPlan, VoiceRender, AudioCompose, and AudioExport own their stage contracts and perform kind-sensitive dispatch. Readio uses one lifecycle for every bundle.

## Bundle workflow

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

`readio bundle run` sequences these stages. The RenderPlan result supplies the exact variant sent unchanged to VoiceRender, AudioCompose, and optional AudioExport. The manual CLI and Python service expose the same specialist stages for workflows that need stage-by-stage control. Readio does not normalize or reconstruct the variant.

A book bundle may contain multiple chapter items; a document bundle represents a single document item. This is a content distinction recorded by the manifest, not a second workspace format or Readio routing policy.

## Ownership

| Concern                                          | Owner                  | Readio's role                                                   |
| ------------------------------------------------ | ---------------------- | --------------------------------------------------------------- |
| Source conversion, `.ssmd-bundle`, manifest kind | SSMDConvert            | Forward conversion requests and resolve validated bundles       |
| Semantic plans and RenderPlan projection         | UtterPlan / RenderPlan | Forward bundle requests and preserve the exact returned variant |
| Profiles, voices, engines, models, and synthesis | VoiceRender            | Delegate management and bundle rendering                        |
| Composition and audio-job artifacts              | AudioCompose           | Forward one bundle compose request and return the native result |
| Encoding and export artifacts                    | AudioExport            | Forward one bundle export request; integration is optional      |
| CLI, sequencing, and playback convenience        | Readio                 | Coordinate public calls and adapt progress                      |

`WorkspaceRef.kind` is informational. Readio does not branch on it, parse manifests, validate chapter selectors, or duplicate specialist bundle rules.

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

The editable bundle and all stage artifacts remain specialist-owned. Readio writes no parallel project state, manifest, stage status, RenderPlan, render manifest, composition job, or export format. Its `WorkspaceRef`, progress events, pipeline results, and bundle status view are in-memory routing/adaptation values only. Status is a derived read-only view over specialist-owned state; Readio persists no stage-status database or project metadata.

## Dependency boundary

Readio imports specialist package-root public APIs only: SSMDConvert, UtterPlan, VoiceRender, AudioCompose, and optional AudioExport. It does not import specialist private submodules or backend SDKs. Playback and audio I/O helpers unrelated to bundle stages remain in Readio.
