---
name: readio
description: Use Readio v0.5 for `.ssmd-bundle` orchestration, direct speech, and delegated VoiceRender controls.
compatibility: Requires Readio and specialist packages for the selected workflow. Playback requires an audio device; synthesis requires an installed VoiceRender engine.
---

# Readio

Use Readio to create or operate SSMDConvert `.ssmd-bundle` workspaces, coordinate specialist speech stages, or speak literal text. The bundle manifest determines whether content is a book or a document; specialists own kind-sensitive dispatch and validation.

## Workflow decisions

1. **Exact speech:** Do not paraphrase requested text. Use `readio speak` or `readio.api.speak` for literal text.
2. **Bundle workflow:** Use `readio bundle convert SOURCE` to create an editable `.ssmd-bundle`. SSMDConvert determines source kind and accepted formats.
3. **Complete pipeline:** Use `readio bundle run SOURCE_OR_BUNDLE --profile PROFILE --language LANG`. The command compiles, projects, renders, and composes; `--export` adds optional AudioExport.
4. **Status before resuming:** Run `readio bundle status BUNDLE` before manually continuing an interrupted workflow. Use its exact variant and next-action argument array; do not guess from filenames.
5. **Stage identity:** Preserve the exact variant returned by UtterPlan when continuing manually. `readio bundle run` forwards the projected variant unchanged; do not invent or normalize it.
6. **Package-specific control:** Use SSMDConvert, UtterPlan, VoiceRender, AudioCompose, or AudioExport CLIs directly for detailed domain options and reports.
7. **Profiles and engines:** Readio management commands delegate to VoiceRender. Install backend extras through VoiceRender or the corresponding Readio pass-through extra.
8. **Agents:** Use explicit bundle/output paths. Progress is on stderr; use `--json` for machine-readable results and errors.

## Basic commands

```bash
python -m pip install readio
python -m pip install "readio[kokoro]"

readio bundle convert chapter.docx -o chapter.ssmd-bundle
readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narrator.renderprofile.toml
readio bundle run chapter.ssmd-bundle --profile narrator.renderprofile.toml \
  --language en-US
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello from Readio."
readio doctor --json
readio bundle status chapter.ssmd-bundle
```

For a book, create the RenderProfile inside the bundle when its provenance should be available during projection:

```bash
readio bundle convert novel.epub -o novel.ssmd-bundle
readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o novel.ssmd-bundle/narrator.renderprofile.toml
readio bundle run novel.ssmd-bundle \
  --profile novel.ssmd-bundle/narrator.renderprofile.toml \
  --language en-US --chapters 5-17
```

The lifecycle commands are documented in [CLI reference](references/cli.md). Input handling is covered in [Input formats](references/input-formats.md); specialist ownership and manual workflows are covered in [SSMD](references/ssmd.md).

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
