---
name: readio
description: Use Readio v0.5 for direct speech and audiobook orchestration over specialist packages.
compatibility: Requires Readio 0.5 and the specialist packages for the selected operation. Playback requires an audio device. Audiobook builds require a VoiceRender RenderProfile and an installed VoiceRender engine.
---

# Readio

Use Readio for literal-text speech or as a convenience layer over the specialist audiobook packages. Specialist artifacts, results, reports, and stage decisions remain authoritative.

## Workflow decisions

1. **Exact speech:** Do not paraphrase requested text. Use `readio speak` for literal text, or `readio.api.speak` when the text is already in a Python string.
2. **Narrated summaries:** Generate the requested summary as plain text, then send that text to `readio speak`. Readio does not provide the former general Markdown or SSMD rendering pipeline.
3. **Audiobooks:** Convert a supported source document to an SSMDConvert `.ssmdbook` workspace, then build it with a VoiceRender RenderProfile.
4. **Package-specific control:** Use the specialist CLIs directly when detailed conversion, compilation, synthesis, composition, or export settings are needed.
5. **Stage identity:** Preserve the exact variant returned by UtterPlan when continuing with specialist commands. Do not invent or normalize it.
6. **Engines:** Install backend extras through VoiceRender, not through Readio. Use `readio doctor --json` to inspect package versions and VoiceRender engine availability.
7. **Agents:** Use explicit workspace and output paths. Use `--json` for audiobook operations and diagnostics when machine-readable results are useful.

## Basic commands

```bash
python -m pip install readio
python -m pip install "voicerender[kokoro]>=0.2.0,<0.3"

readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello from Readio."
readio audiobook convert novel.epub --output novel.ssmdbook
readio audiobook build novel.ssmdbook --profile narration.renderprofile.toml \
  --output novel.m4b --json
readio doctor --json
```

`speak` synthesizes literal text through VoiceRender and plays the returned audio through the current device. `audiobook build` orchestrates the specialist stages and returns specialist-owned results. The available CLI surface is documented in [CLI reference](references/cli.md).

## Specialist workflow

For direct control, use the package CLIs and pass the exact UtterPlan variant unchanged to later stages:

```bash
ssmdconvert book convert novel.epub -o novel.ssmdbook
utterplan compile-book novel.ssmdbook
voicerender profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narration.renderprofile.toml
utterplan render-book novel.ssmdbook --profile narration.renderprofile.toml

VARIANT="<exact variant returned by utterplan render-book>"
voicerender render-book novel.ssmdbook --variant "$VARIANT"
audiocompose compose-book novel.ssmdbook --variant "$VARIANT"
audioexport export-book novel.ssmdbook --variant "$VARIANT" --output novel.m4b
```

SSMDConvert owns workspaces and conversion. UtterPlan owns compilation and RenderPlan projection. VoiceRender owns voice resolution and synthesis. AudioCompose owns composition. AudioExport owns encoded exports. See [SSMD and specialist ownership](references/ssmd.md).

## Boundaries

Readio 0.5 is a clean break. It does not expose the former generic project, render, synth, engine catalog, defaults, configuration, or Spotify commands. It does not read or migrate `.readio` state and provides no compatibility aliases. Start from source material and create an SSMDConvert workspace, or use the specialist packages directly.

Readio does not manage backend models or voices, publish to Spotify, or create specialist reports. For installation and runtime problems, see [troubleshooting](references/troubleshooting.md). The former Spotify reference is retained only to make the removed integration boundary explicit.
