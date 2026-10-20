# CLI reference

Readio's CLI creates or resolves native `.ssmdoc` and `.ssmdbook` workspaces and dispatches each stage to its owning specialist package. Use `readio --help` and command-specific help for the installed option set.

```bash
readio --help
readio init --help
readio plan --help
readio render-plan --help
readio run --help
readio speak --help
readio doctor --help
```

## Native workspace workflows

### Create and plan

```bash
readio init article.docx -o article.ssmdoc
readio plan article.ssmdoc --language en-US
readio init novel.epub -o novel.ssmdbook
readio plan novel.ssmdbook --language en-US --chapters 5-17
```

`init` delegates conversion and bundle writing to SSMDConvert. It infers EPUB as a book and other supported inputs as documents unless `--kind` is supplied. Outputs use the matching `.ssmdoc` or `.ssmdbook` suffix. `plan` delegates compilation to UtterPlan; its planner options include language, diagnostics, text preparation, overlap/renderability policy, and optional spaCy controls.

### Manual specialist workflow

```bash
readio render-plan article.ssmdoc --profile narrator.renderprofile.toml --json
VARIANT="<exact variant returned by render-plan>"
readio render article.ssmdoc --variant "$VARIANT"
readio compose article.ssmdoc --variant "$VARIANT"
readio export article.ssmdoc --variant "$VARIANT" --output article.m4a
```

`render-plan` delegates profile-specific projection to UtterPlan and emits the specialist variant. When running stages manually, pass that exact variant unchanged to `render`, `compose`, and `export`. `render` delegates to VoiceRender; `compose` to AudioCompose; `export` to AudioExport. The export stage requires the optional AudioExport integration (install `readio[audioexport]`).

### Run the complete pipeline

```bash
readio run article.docx --profile narrator.renderprofile.toml \
  --language en-US --json
readio run novel.ssmdbook \
  --profile novel.ssmdbook/narrator.renderprofile.toml \
  --chapters 5-17 --language en-US --export --output novel.m4b
```

`run` can accept source material or an existing workspace. It creates/resolves the native workspace, compiles, projects the RenderPlan, renders, and composes. `--export` adds the optional export stage. Readio forwards UtterPlan's projected variant unchanged across all downstream calls. Stage-specific roots, policies, force flags, chapter selection, metadata, and encoder paths are forwarded to specialist APIs rather than reimplemented.

## Voice and profile management

These command groups delegate to VoiceRender:

- `engines` and `models` discover, inspect, install, verify, and remove engine models.
- `voices` discovers voices; `voice source` creates and inspects voice-source artifacts; `voice clone` creates a specialist RenderProfile.
- `profile` creates, inspects, validates, refreshes, and manages RenderProfile roles.

Use the command-specific `--help` output for selectors and options. Install engine backends through VoiceRender extras, for example `python -m pip install "voicerender[kokoro]"`.

## Direct speech

```bash
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello."
readio speak --profile narrator.renderprofile.toml --role narrator \
  --output greeting.wav --no-play "Hello."
```

`speak` synthesizes literal text through VoiceRender and plays by default. `--output` writes WAV and `--no-play` skips local playback. Use `--json` for a machine-readable result.

## Dependency diagnostics

```bash
readio doctor
readio doctor --json
```

Doctor reports specialist package versions and VoiceRender engine availability. Progress is written to stderr; JSON results and structured errors are written to stdout. `--no-progress` disables progress callbacks on workflows that support them.

## Removed commands

The current CLI has no `config`, `audiobook`, or former generic project/plan/render-manifest command groups. Readio does not read a `readio.toml` or `.readio` state, and provides no migration path for its former project format. Use `readio init` to create a new native workspace from source material, or use the specialist packages directly.
