# CLI reference

Use `readio --help` and each command's `--help` for the installed options. Readio delegates domain behavior and persisted artifacts to SSMDConvert, UtterPlan, VoiceRender, AudioCompose, and optional AudioExport.

## Native workspaces and pipeline

```bash
readio init chapter.docx -o chapter.ssmdoc
readio plan chapter.ssmdoc --language en-US
readio render-plan chapter.ssmdoc --profile narrator.renderprofile.toml --json
readio run chapter.ssmdoc --profile narrator.renderprofile.toml --language en-US
```

`init` creates or resolves a native `.ssmdoc` or `.ssmdbook`. `plan` compiles with UtterPlan. `render-plan` creates a profile-specific projection. For manual stages, pass the exact returned `variant` unchanged to:

```bash
VARIANT="<exact variant returned by render-plan>"
readio render chapter.ssmdoc --variant "$VARIANT"
readio compose chapter.ssmdoc --variant "$VARIANT"
readio export chapter.ssmdoc --variant "$VARIANT" --output chapter.m4a
```

`run` sequences workspace creation/resolution, planning, projection, synthesis, and composition. It forwards UtterPlan's exact projected variant. `--export` opts into AudioExport; install it with `readio[audioexport]`. Specialist output roots and policies are forwarded rather than reimplemented. Progress uses stderr and `--json` keeps machine-readable results on stdout.

## Voice and profile controls

- `engines` and `models` delegate discovery and model management to VoiceRender.
- `voices` discovers voices; `voice source` and `voice clone` create or inspect specialist artifacts.
- `profile` creates, validates, refreshes, and manages RenderProfile logical roles.

Install backends through VoiceRender extras, for example `python -m pip install "voicerender[kokoro]"`. Use each command's `--help` for selectors and options.

## Direct speech and diagnostics

```bash
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello."
readio speak --profile narrator.renderprofile.toml --role narrator \
  --output greeting.wav --no-play "Hello."
readio doctor --json
```

`speak` synthesizes literal text through VoiceRender and plays by default. `--output` writes WAV; `--no-play` skips playback. Doctor reports specialist versions and VoiceRender engine availability.

## Clean break

There is no `config` or `audiobook` command. Readio does not read `readio.toml`, `.readio` state, or old Readio project manifests, and it has no migration path. Create a new native workspace from source material with `readio init`.
