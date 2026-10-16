# Readio architecture

Readio coordinates a small set of specialist packages. The specialist that owns a stage also owns its validation, artifacts, reports, reuse rules, and detailed errors. Readio does not create a parallel project manifest or stage state.

## Manual specialist workflow

The canonical book workflow is:

```text
EPUB
  │
  ▼
SSMDConvert
  │  converts the source and owns the .ssmdbook workspace
  ▼
UtterPlan
  │  compiles semantic plans and projects a RenderPlan variant
  ▼
VoiceRender
  │  resolves the profile and synthesizes the selected chapters
  ▼
AudioCompose
  │  composes chapter audio and applies composition policy
  ▼
AudioExport
     encodes the composed book, including M4B assembly
```

| Concern                                           | Owner                    | Readio's role                                                        |
| ------------------------------------------------- | ------------------------ | -------------------------------------------------------------------- |
| Source conversion and `.ssmdbook` layout          | SSMDConvert              | Call public conversion and workspace APIs                            |
| Semantic compilation and chapter selection        | UtterPlan                | Forward the selector and compile options                             |
| RenderPlan and variant projection                 | UtterPlan and RenderPlan | Pass the returned variant unchanged to later stages                  |
| Voice and backend resolution, capacity, synthesis | VoiceRender              | Call public resolve, session, and book-render APIs                   |
| Composition, pause policy, emphasis, and loudness | AudioCompose             | Forward book composition options                                     |
| Encoding, metadata, cover art, and M4B output     | AudioExport              | Forward export options and output path                               |
| Cross-stage convenience                           | Readio                   | Coordinate calls, expose progress, and return the specialist results |

A user can stop after any specialist stage and continue with its owning CLI. Readio does not mirror specialist result models or parse their reports to decide whether a stage is current.

## Readio orchestration

`readio audiobook convert` uses SSMDConvert to convert an EPUB and write a specialist-owned workspace. `readio audiobook build` runs the selected stages in order:

```text
SSMDConvert workspace
  → UtterPlan compile_book
  → UtterPlan render_book
  → VoiceRender render_book
  → AudioCompose compose_book
  → AudioExport export_book
```

The variant returned by `utterplan.render_book` is passed unchanged to VoiceRender, AudioCompose, and AudioExport. Readio's in-memory `BookBuildResult` holds those original stage result objects; it is not a new stage manifest. `force`, `fail_fast`, chapter selection, profile, output, and stop-after options are forwarded where the specialist APIs support them.

`readio speak` is a separate convenience path for literal text. It parses an optional neutral `engine:target/voice` selector, calls VoiceRender's public resolve/session/synthesis APIs, then passes the returned samples and sample rate to Readio's generic audio-device playback helper. It does not use UtterPlan, book composition, or AudioExport.

`readio doctor` reports installed specialist package versions and engine availability through VoiceRender's public engine-list and engine-status functions. Readio does not inspect private specialist modules or load backend implementations for the report.

## Persistent state and compatibility

The `.ssmdbook` workspace and all stage artifacts are specialist-owned. Readio writes no project manifest, cache, stage status, plan, render manifest, or compatibility metadata.

Version 0.5.0 is a clean break. Readio no longer supports its former generic `*.readio` projects, `project.json`, old plan/render-manifest formats, project migrations, or old project pipeline APIs. There is no migration command. Existing legacy files are not imported into `.ssmdbook` workspaces. Convert the original source with SSMDConvert or use the specialist packages directly.

## Dependency boundary

Readio directly depends on the specialist package roots it calls, plus NumPy for audio arrays and sounddevice for local playback. Engine extras are installed through VoiceRender, for example `voicerender[kokoro]`. Readio does not pin backend packages or depend directly on Voicefolio. Its optional `spacy` and `documents` extras only pass through UtterPlan and SSMDConvert extras.
