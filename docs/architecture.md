# Readio architecture

Readio is a thin orchestration layer over specialist public APIs. Each specialist package owns its domain schemas, validation, persisted artifacts, reports, and reuse rules. Readio owns workspace routing, request sequencing, CLI convenience, and small in-memory event/result adapters.

## Readio orchestration

### Document workspaces

```text
source document
  → SSMDConvert: convert to native .ssmdoc
  → UtterPlan: compile semantic speech plan
  → UtterPlan / RenderPlan: project a profile-specific variant
  → VoiceRender: preflight and synthesize
  → AudioCompose: compose WAV output
  → AudioExport: optionally encode a delivery format
```

### Book workspaces

```text
EPUB
  → SSMDConvert: convert to native .ssmdbook
  → UtterPlan: compile chapters and project a variant
  → VoiceRender: render selected chapters
  → AudioCompose: compose book audio and chapter timeline
  → AudioExport: optionally encode the book
```

`readio run` sequences the stages for either workspace kind. Its RenderPlan result supplies the exact variant sent to VoiceRender, AudioCompose, and AudioExport. The manual CLI and Python service expose the same specialist stages for workflows that need stage-by-stage control. Readio does not normalize or reconstruct the variant.

### Direct speech and management

`readio speak` is a separate literal-text path through VoiceRender and local playback; it does not create a workspace or invoke planning, composition, or export. Engine/model, voice/source, and RenderProfile operations delegate to VoiceRender's public management APIs. Backend engines are installed through VoiceRender extras.

| Concern                                          | Owner                  | Readio's role                                                       |
| ------------------------------------------------ | ---------------------- | ------------------------------------------------------------------- |
| Document/book conversion and native bundles      | SSMDConvert            | Select the workspace kind and forward conversion options            |
| Semantic plans and RenderPlan projection         | UtterPlan / RenderPlan | Forward planner and profile requests; preserve the returned variant |
| Profiles, voices, engines, models, and synthesis | VoiceRender            | Delegate management and render calls                                |
| Composition and audio-job artifacts              | AudioCompose           | Forward options and return the native result                        |
| Encoding and export artifacts                    | AudioExport            | Forward supported options; the export extra is optional             |
| CLI, sequencing, playback convenience            | Readio                 | Coordinate public calls and adapt progress                          |

## Manual specialist workflow

Use specialist CLIs directly when package-specific controls or detailed diagnostics are needed. Preserve the exact variant returned by UtterPlan:

```bash
ssmdconvert book convert novel.epub -o novel.ssmdbook
utterplan compile-book novel.ssmdbook
voicerender profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o novel.ssmdbook/narrator.renderprofile.toml
utterplan render-book novel.ssmdbook \
  --profile novel.ssmdbook/narrator.renderprofile.toml

VARIANT="<exact variant returned by utterplan render-book>"
voicerender render-book novel.ssmdbook --variant "$VARIANT"
audiocompose compose-book novel.ssmdbook --variant "$VARIANT"
audioexport export-book novel.ssmdbook --variant "$VARIANT" --output novel.m4b
```

For Readio's equivalent orchestration, see the [CLI reference](cli.md). For detailed specialist options, consult the owning package documentation.

## Persistent state and compatibility

`.ssmdoc` and `.ssmdbook` bundles and all stage artifacts remain specialist-owned. Readio writes no parallel project state, project manifest, stage status, RenderPlan, render manifest, composition job, or export format. Its `WorkspaceRef`, progress events, and pipeline results are in-memory routing/adaptation values only.

Version 0.5 is a clean break from Readio's former project and pipeline formats. Old Readio project directories and settings are unsupported and are not read or attached. There is no migration command, compatibility alias, or runtime shim. Recreate work from source material with SSMDConvert, or use the specialist packages directly.

## Dependency boundary

Readio imports specialist package-root public APIs only: SSMDConvert, UtterPlan, VoiceRender, AudioCompose, and optional AudioExport. It does not import specialist private submodules or backend SDKs. The dependency-boundary tests enforce this rule and guard against reintroducing legacy persistence identifiers. Playback and audio I/O helpers unrelated to workspace stages remain in Readio.
