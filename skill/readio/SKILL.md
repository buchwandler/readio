---
name: readio
description: Use Readio v0.5 for direct speech, single-document rendering, and audiobook orchestration over specialist packages.
compatibility: Requires Readio 0.5 and the specialist packages for the selected operation. Playback requires an audio device. Rendering requires a VoiceRender RenderProfile and an installed compatible engine.
---

# Readio

Use Readio for literal-text speech or as a convenience layer over specialist document and audiobook packages. Specialist schemas, artifacts, results, reports, and stage decisions remain authoritative.

## Workflow decisions

1. **Exact speech:** Do not paraphrase requested text. Use `readio speak` for literal text, or `readio.api.speak` when the text is already in Python.
2. **One document:** Use `readio render SOURCE --profile PROFILE` for a supported document. It delegates conversion, planning, synthesis, composition, and optional export. Use `--stop-after` or retained artifacts to inspect intermediate work.
3. **Audiobooks:** Convert a supported EPUB to an SSMDConvert `.ssmdbook` workspace, then build it with a VoiceRender RenderProfile.
4. **Package-specific control:** Use specialist CLIs directly for detailed conversion, compilation, synthesis, composition, and export settings.
5. **Stage identity:** Preserve the exact variant returned by UtterPlan when continuing with specialist commands. Do not invent or normalize it.
6. **Engines and voices:** Readio management commands delegate to VoiceRender. Install backend extras through VoiceRender, not Readio; use `readio doctor --json` for package and engine availability.
7. **Settings:** Readio reads `readio.toml` only when the user explicitly supplies `--config PATH`. Precedence is specialist defaults < settings file < CLI flags.
8. **Agents:** Use explicit workspace/output paths. Progress is on stderr; use `--json` for machine-readable results and errors without mixing progress into stdout.

## Basic commands

```bash
python -m pip install readio
python -m pip install "voicerender[kokoro]>=0.2.0,<0.3"

readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello from Readio."
readio render chapter.docx --profile narrator.renderprofile.toml \
  --output chapter.m4a --keep-artifacts
readio audiobook convert novel.epub --output novel.ssmdbook --chapters 5-17
readio audiobook build novel.ssmdbook --profile narrator.renderprofile.toml \
  --output novel.m4b --json
readio config init --output readio.toml
readio doctor --json
```

`speak` synthesizes literal text through VoiceRender and plays by default. `readio render` uses the specialist stage chain and may stop after conversion, planning, RenderPlan projection, synthesis, composition, or export. The available commands are documented in [CLI reference](references/cli.md); source inputs are covered in [Input formats](references/input-formats.md).

## Specialist workflow

For direct stage control, use specialist CLIs and pass UtterPlan's exact variant unchanged:

```bash
ssmdconvert book convert novel.epub -o novel.ssmdbook
utterplan compile-book novel.ssmdbook
voicerender profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narrator.renderprofile.toml
utterplan render-book novel.ssmdbook --profile narrator.renderprofile.toml

VARIANT="<exact variant returned by utterplan render-book>"
voicerender render-book novel.ssmdbook --variant "$VARIANT"
audiocompose compose-book novel.ssmdbook --variant "$VARIANT"
audioexport export-book novel.ssmdbook --variant "$VARIANT" --output novel.m4b
```

SSMDConvert owns conversion and `.ssmdbook` workspaces. UtterPlan owns semantic plans and variants. RenderPlan owns its public contract and codec. VoiceRender owns profiles, voice/engine management, and synthesis. AudioCompose owns composition and audio-job artifacts. AudioExport owns encoding and FFmpeg process handling.

## Boundaries

Readio does not implement backends, DSP, encoders, model registries, specialist validation, or a parallel RenderPlan/manifest. It writes no `.readio` project state and provides no compatibility aliases for the removed generic project pipeline. Readio does not publish to Spotify. For input support, specialist ownership, and troubleshooting, see the linked references.
