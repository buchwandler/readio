# CLI reference

Use `readio --help`, `readio bundle --help`, and command-specific help for installed options. Readio delegates domain behavior and persisted artifacts to SSMDConvert, UtterPlan, VoiceRender, AudioCompose, and optional AudioExport.

## Bundle lifecycle

```bash
readio bundle convert chapter.docx -o chapter.ssmd-bundle
readio bundle compile chapter.ssmd-bundle --language en-US
readio bundle render-plan chapter.ssmd-bundle --profile narrator.renderprofile.toml
readio bundle run chapter.ssmd-bundle --profile narrator.renderprofile.toml --language en-US
```

All SSMD lifecycle operations use editable `.ssmd-bundle` directories. `convert` delegates source handling and bundle creation to SSMDConvert. `compile` delegates to UtterPlan. `render-plan` projects a profile-specific RenderPlan; when continuing manually, pass its exact variant unchanged:

```bash
VARIANT="<exact variant returned by render-plan>"
readio bundle render chapter.ssmd-bundle --variant "$VARIANT"
readio bundle compose chapter.ssmd-bundle --variant "$VARIANT" --save-audiojobs jobs.json
readio bundle export chapter.ssmd-bundle --variant "$VARIANT" --output chapter.m4a
```

`run` sequences conversion or bundle resolution, planning, projection, synthesis, and composition. `--export` opts into AudioExport; install it with `readio[audioexport]`. Progress uses stderr and `--json` keeps machine-readable results on stdout. Use `--no-progress` where supported.

## Voice and profile controls

- `engines` and `models` delegate discovery and model management to VoiceRender.
- `voices` discovers voices; `voice source` and `voice clone` create or inspect specialist artifacts.
- `profile` creates, validates, refreshes, and manages RenderProfile logical roles.

Install backends through VoiceRender extras, for example `python -m pip install "voicerender[kokoro]"`. Use each command's help for selectors and options.

## Direct speech and diagnostics

```bash
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello."
readio speak --profile narrator.renderprofile.toml --role narrator \
  --output greeting.wav --no-play "Hello."
readio doctor --json
```

`speak` synthesizes literal text through VoiceRender and plays by default. `--output` writes WAV; `--no-play` skips playback. Doctor reports specialist versions and VoiceRender engine availability.
