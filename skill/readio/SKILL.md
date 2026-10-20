---
name: readio
description: Use Readio v0.5 for native `.ssmdoc`/`.ssmdbook` orchestration, direct speech, and delegated VoiceRender controls.
compatibility: Requires Readio and specialist packages for the selected workflow. Playback requires an audio device; synthesis requires an installed VoiceRender engine.
---

# Readio

Use Readio to create or operate native SSMDConvert workspaces, coordinate specialist speech stages, or speak literal text. Specialist schemas, artifacts, results, reports, and stage decisions remain authoritative.

## Workflow decisions

1. **Exact speech:** Do not paraphrase requested text. Use `readio speak` or `readio.api.speak` for literal text.
2. **Native workspace:** Use `readio init SOURCE` to create `.ssmdoc` or `.ssmdbook` bundles. EPUB defaults to a book workspace; other supported inputs default to a document workspace.
3. **Complete pipeline:** Use `readio run WORKSPACE --profile PROFILE --language LANG`. The command compiles, projects, renders, and composes; `--export` adds optional AudioExport.
4. **Stage identity:** Preserve the exact variant returned by UtterPlan when continuing manually. `readio run` forwards the projected variant unchanged; do not invent or normalize it.
5. **Package-specific control:** Use SSMDConvert, UtterPlan, VoiceRender, AudioCompose, or AudioExport CLIs directly for detailed domain options and reports.
6. **Profiles and engines:** Readio management commands delegate to VoiceRender. Install backend extras through VoiceRender or the corresponding Readio pass-through extra.
7. **Clean break:** Existing Readio project directories, settings, plans, manifests, and stage records are unsupported. There is no migration command or compatibility layer. Start from source material.
8. **Agents:** Use explicit workspace/output paths. Progress is on stderr; use `--json` for machine-readable results and errors.

## Basic commands

```bash
python -m pip install readio
python -m pip install "readio[kokoro]"

readio init chapter.docx -o chapter.ssmdoc
readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narrator.renderprofile.toml
readio run chapter.ssmdoc --profile narrator.renderprofile.toml --language en-US
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello from Readio."
readio doctor --json
```

For books, create the workspace first and store the RenderProfile in it:

```bash
readio init novel.epub -o novel.ssmdbook
readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o novel.ssmdbook/narrator.renderprofile.toml
readio run novel.ssmdbook --profile novel.ssmdbook/narrator.renderprofile.toml \
  --language en-US --export --output novel.m4b
```

The current commands are documented in [CLI reference](references/cli.md). Input types are covered in [Input formats](references/input-formats.md); specialist ownership and manual workflows are covered in [SSMD](references/ssmd.md).

## Specialist workflow

For direct stage control, pass UtterPlan's exact variant unchanged:

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

SSMDConvert owns conversion and native workspaces. UtterPlan owns semantic plans and variants. RenderPlan owns its public contract. VoiceRender owns profiles, voice/engine management, and synthesis. AudioCompose owns composition and audio-job artifacts. AudioExport owns encoding. Readio writes no competing project state and provides no migration for former Readio formats.
