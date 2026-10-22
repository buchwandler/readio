---
name: readio
description: Use Readio v0.5 for `.ssmd-bundle` orchestration, direct speech, and delegated VoiceRender controls.
compatibility: Requires Readio and specialist packages for the selected workflow. Playback requires an audio device; synthesis requires an installed VoiceRender engine.
---

# Readio

Use Readio's user-owned render-intent workflow for complete book/document renders, or operate lower-level SSMDConvert `.ssmd-bundle` stages when needed. Keep the Readio intent TOML separate from the VoiceRender RenderProfile; the bundle manifest determines content kind, and specialists own kind-sensitive dispatch and validation.

## Workflow decisions

1. **Exact speech:** Do not paraphrase requested text. Use `readio speak` or `readio.api.speak` for literal text.
2. **Complete render:** Create/review an intent with `readio intent book|document create`, then run `readio render --config PATH`. Use `readio intent show` and `validate` before rendering.
3. **One-off render:** Use `readio render SOURCE --profile PROFILE` when a persistent Readio intent is unnecessary; the VoiceRender RenderProfile remains a separate input.
4. **Safe reruns:** Existing bundles are reused. Rerun only with `--force-stage STAGE` or `--restart-from STAGE`; forcing export is required to replace existing final output. Export tools are preflighted before conversion.
5. **Intent status:** Use `readio status --config PATH` for a saved-intent overview. For manual stage-by-stage recovery, use `readio bundle status BUNDLE` and follow its exact next-action arguments.
6. **Stage identity:** Preserve UtterPlan's exact projected variant through VoiceRender, AudioCompose, and AudioExport; never invent or normalize it.
7. **Advanced control:** `readio bundle run` and individual `readio bundle` commands remain available; specialist CLIs provide package-specific details and reports.
8. **Profiles and agents:** Profile/engine commands delegate to VoiceRender. Use explicit user-owned intent, profile, bundle, and output paths. Progress is on stderr; use `--json` for machine-readable results.

## Intent-first commands

```bash
python -m pip install readio
python -m pip install "readio[kokoro]"

readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narrator.renderprofile.toml
readio intent document create chapter.docx --profile narrator.renderprofile.toml --language en-US
readio intent show chapter.readio.toml --resolved
readio intent validate chapter.readio.toml
readio render --config chapter.readio.toml --dry-run
readio render --config chapter.readio.toml
readio status --config chapter.readio.toml
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello from Readio."
readio doctor --json
```

Book intents default to M4B; document intents default to MP3. `readio render SOURCE --profile PROFILE` is the one-off alternative. Intent TOML is user-owned and separate from the VoiceRender RenderProfile; Readio creates no `.readio/` workspace state. AudioExport is optional (`readio[audioexport]`), and selected format/FFmpeg/FFprobe readiness is checked before conversion.

For low-level lifecycle or stage-by-stage control, retain the existing `readio bundle convert`, `compile`, `render-plan`, `render`, `compose`, `export`, `status`, and `run` commands:

```bash
readio bundle run chapter.docx --bundle-output chapter.ssmd-bundle \
  --profile narrator.renderprofile.toml --language en-US
readio bundle status chapter.ssmd-bundle
```

The complete command catalog is in [CLI reference](references/cli.md). Input handling is covered in [Input formats](references/input-formats.md); specialist ownership and manual workflows are covered in [SSMD](references/ssmd.md).

## Specialist workflow

For direct stage control, pass UtterPlan's exact variant unchanged:

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

SSMDConvert owns conversion and bundles. UtterPlan owns semantic plans and variants. RenderPlan owns its public contract. VoiceRender owns profiles, voice/engine management, and synthesis. AudioCompose owns composition and audio-job artifacts. AudioExport owns encoding.
