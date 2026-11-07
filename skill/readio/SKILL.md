---
name: readio
description: Use Readio v0.6 for unified speech workflows, explicit temporary or persistent workspaces, and delegated VoiceRender controls.
compatibility: Requires Readio and specialist packages for the selected workflow. Playback requires an audio device; synthesis requires an installed VoiceRender engine.
---

# Readio

Use Readio's user-owned `readio.render.v2` intent for complete file-to-audio workflows. Keep the intent TOML separate from the VoiceRender RenderProfile; specialists own their schemas, validation, artifacts, and kind-sensitive dispatch.

## Workflow decisions

1. **Exact speech:** Do not paraphrase requested text. Use `readio speak` or `readio.api.speak` for literal text.
2. **Complete render:** Create/review an intent with `readio intent book|document create`, then run `readio render --config PATH`. Use `readio intent show` and `validate` before rendering.
3. **One-off render:** Use `readio render SOURCE --profile PROFILE` when a saved intent is unnecessary; the VoiceRender RenderProfile remains a separate input.
4. **Temporary by default:** Without an explicit `--bundle`, Readio uses a unique temporary workspace and cleans it after execution. It never invents a persistent bundle path or hidden `.readio/` state.
5. **Persistent state:** Supply `--bundle PATH`, set `[workspace] mode = "bundle"` with `workspace.bundle` in an intent, or pass an existing `.ssmd-bundle` as the source. Persistent book creation converts the complete source inventory; chapter selection scopes downstream work.
6. **Inspect before execution:** `readio render --dry-run` and `readio status` share read-only workflow inspection. Use `readio status --config PATH` for a saved intent or `readio status BUNDLE --profile PROFILE` for an existing bundle.
7. **Safe reruns:** Use `--rebuild-from STAGE` for explicit rebuilds and `--replace-output` separately to authorize final-output replacement. Specialist inspection decides what is safe; Readio fails closed on unknown unsafe state.
8. **Stage identity:** Preserve UtterPlan's exact projected variant through VoiceRender, AudioCompose, and AudioExport; never invent or normalize it.
9. **Profiles and agents:** Profile/engine commands delegate to VoiceRender. Use explicit user-owned intent, profile, bundle, and output paths. Progress is on stderr; use `--json` for machine-readable results.

## Unified workflow commands

```bash
python -m pip install readio
python -m pip install "readio[kokoro]"

readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narrator.renderprofile.toml
readio intent document create chapter.docx \
  --profile narrator.renderprofile.toml --language en-US
readio intent show chapter.readio.toml --resolved
readio intent validate chapter.readio.toml
readio render --config chapter.readio.toml --dry-run
readio render --config chapter.readio.toml
readio status --config chapter.readio.toml

# Persistent workspace; first book conversion includes every source chapter.
readio render novel.epub --bundle novel.ssmd-bundle \
  --profile narrator.renderprofile.toml --language en-US --chapters 5-17
```

Book intents default to M4B; document intents default to MP3. Intent TOML is user-owned and separate from the VoiceRender RenderProfile. AudioExport is included in the core installation; Readio checks selected format and tool readiness before conversion.

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
