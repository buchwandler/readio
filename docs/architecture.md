# Readio architecture

Readio is a user-facing orchestrator over specialist packages. The package that owns a stage also owns its schema, validation, persisted artifacts, reports, reuse rules, and detailed errors. Readio forwards requests and returns specialist result objects; it does not build a parallel project model.

## Ownership and workflows

### One document

```text
source document
  → SSMDConvert document conversion
  → UtterPlan semantic compilation
  → UtterPlan projection to RenderPlan
  → VoiceRender preflight and synthesis
  → AudioCompose WAV composition
  → AudioExport encoding
```

`readio render` runs these stages in order and can stop after `convert`, `plan`, `render-plan`, `synth`, `compose`, or `export`. The RenderPlan, preflight report, render files, composition job, and encoded export use the public specialist APIs and codecs. The in-memory `DocumentRenderResult` retains the original specialist results.

### Audiobooks

```text
EPUB
  → SSMDConvert conversion and .ssmdbook workspace
  → UtterPlan book compilation and RenderPlan projection
  → VoiceRender book synthesis
  → AudioCompose book composition
  → AudioExport book encoding
```

The exact variant returned by UtterPlan is passed unchanged to VoiceRender, AudioCompose, and AudioExport. `readio audiobook build` exposes the supported public options of each stage and preserves their result objects.

### Direct speech and management

`readio speak` is a separate literal-text path through VoiceRender and local playback; it does not invoke planning, composition, or export. Engine/model, voice/source, and RenderProfile operations are thin calls to VoiceRender's public management APIs. Readio owns no backend implementation or model registry.

| Concern                                   | Owner                    | Readio's role                                                         |
| ----------------------------------------- | ------------------------ | --------------------------------------------------------------------- |
| Document and book conversion              | SSMDConvert              | Forward native options and use its workspace writer                   |
| Planning and RenderPlan projection        | UtterPlan                | Forward native planning requests and preserve the returned variant    |
| RenderPlan, profile, and preflight codecs | RenderPlan / VoiceRender | Use public package codecs and validation APIs                         |
| Voice, engine, model, and synthesis       | VoiceRender              | Delegate discovery, lifecycle, role/profile operations, and rendering |
| Composition and audio-job artifacts       | AudioCompose             | Forward compose options and return the native result                  |
| Encoding and export artifacts             | AudioExport              | Forward supported output options                                      |
| Convenience, CLI, and stage selection     | Readio                   | Coordinate public calls and adapt progress to a small event type      |

## Settings and artifact lifecycle

User settings are read only when a command explicitly receives `--config PATH`; Readio does not discover or create settings implicitly. Defaults supplied by specialist constructors are overridden by the selected settings file, then by CLI flags. Profile engine and voice identity remains authoritative.

For `readio render`, an explicit `--artifacts DIR` is retained. `--keep-artifacts` retains outputs in `<source-stem>.render`; without either option, a temporary directory is removed when the call ends. `--clean-artifacts` is restricted to the expected default directory and recognized stage artifacts. Readio never recursively deletes an arbitrary path supplied by the caller. Failures leave completed artifacts in retained directories.

Progress callbacks preserve the original event as `native`; JSON results are written to stdout while progress is written to stderr. Specialist exceptions and stable specialist error codes are not translated.

## Persistent state and compatibility

`.ssmdbook` workspaces and all stage artifacts remain specialist-owned. Readio writes no `.readio` project state, stage manifest, competing RenderPlan, or render manifest. Its result types are in-memory orchestration summaries only.

Version 0.5 is a clean break from Readio's former generic project, plan, and render-manifest formats. There are no compatibility aliases or migration commands. Start from source material with SSMDConvert or use the specialist packages directly.

## Dependency boundary

Readio uses public APIs from SSMDConvert, UtterPlan, RenderPlan, VoiceRender, AudioCompose, and AudioExport (including UtterPlan's TOML codec and VoiceRender's voice-design API), plus its normal support dependencies and NumPy/sounddevice for local playback. Backend engines are installed through VoiceRender extras. Readio does not import backend SDKs or specialist private implementation modules.
