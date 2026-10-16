# CLI reference

Readio exposes four operations: `speak`, `audiobook convert`, `audiobook build`, and `doctor`.

```bash
readio --help
readio speak --help
readio audiobook --help
readio audiobook convert --help
readio audiobook build --help
readio doctor --help
```

## Manual specialist workflow

For package-specific control, run the specialist CLIs directly. The `VARIANT` value must be the exact variant returned by UtterPlan and must be passed unchanged to every later stage.

```bash
ssmdconvert book convert novel.epub --chapters 5-17 -o novel.ssmdbook
utterplan compile-book novel.ssmdbook --chapters 5-17
voicerender profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narration.renderprofile.toml
utterplan render-book novel.ssmdbook --chapters 5-17 \
  --profile narration.renderprofile.toml

VARIANT="<exact variant returned by utterplan render-book>"
voicerender render-book novel.ssmdbook --chapters 5-17 --variant "$VARIANT" \
  --emphasis-policy defer-to-compose
audiocompose compose-book novel.ssmdbook --chapters 5-17 --variant "$VARIANT"
audioexport export-book novel.ssmdbook --chapters 5-17 --variant "$VARIANT" \
  --output novel.m4b
```

Consult each specialist's `--help` for its complete options and diagnostics.

## Direct speech

`speak` joins positional arguments with spaces, resolves them through VoiceRender, synthesizes literal text, and plays the returned audio through the current device.

```bash
readio speak "Hello from Readio."
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello from Readio."
readio speak --engine inflect --target micro-v2 --voice default --lang en "Hello."
```

Options:

- `--voice SELECTOR`: neutral VoiceRender selector in `engine:target[/voice]` form, or a voice value interpreted by VoiceRender.
- `--engine ENGINE` and `--target TARGET`: explicit engine and target. These override the corresponding fields in `--voice`.
- `--lang LANGUAGE`: language passed to VoiceRender. Defaults to `en-US`.
- `--speaker SPEAKER`: optional VoiceRender speaker selector.
- `--offline`: restrict resolution to locally available assets.
- `--refresh`: request refreshed VoiceRender metadata.
- `--engine-option KEY=VALUE`: repeatable engine-specific option forwarded to VoiceRender.

Install backend extras through VoiceRender, for example `python -m pip install "voicerender[kokoro]"`. Playback requires a working audio device.

## Audiobook conversion

```bash
readio audiobook convert novel.epub
readio audiobook convert novel.epub --output novel.ssmdbook --chapters 5-17 --lang en-US
readio audiobook convert novel.epub --json
```

The default workspace path replaces the source suffix with `.ssmdbook`. Conversion and workspace structure belong to SSMDConvert. `--chapters` and `--lang` are forwarded to its public API.

## Audiobook build

```bash
readio audiobook build novel.ssmdbook --profile narration.renderprofile.toml
readio audiobook build novel.ssmdbook --profile narration.renderprofile.toml \
  --chapters 5-17 --output novel.m4b
readio audiobook build novel.ssmdbook --profile narration.renderprofile.toml \
  --stop-after synth --json
```

The build calls UtterPlan compile and RenderPlan projection, VoiceRender synthesis, AudioCompose composition, then AudioExport. Use the exact RenderPlan variant returned by UtterPlan when resuming with specialist tools.

Options include `--chapters`, `--profile`, `--variant`, `--force`, `--fail-fast`, `--stop-after`, `--output`, `--export-profile`, `--cover`, `--bitrate`, `--json`, and `--no-progress`. `--stop-after` accepts `compile`, `render-plan`, `synth`, `compose`, or `export`. The profile is a VoiceRender RenderProfile. Use specialist profiles and CLIs for more detailed backend, composition, or export settings.

JSON output contains the workspace path, returned variant, and the names of stages whose specialist result objects were returned. Readio does not interpret specialist report schemas or persist a parallel stage status.

## Dependency diagnostics

```bash
readio doctor
readio doctor --json
```

The doctor reports installed versions of SSMDConvert, UtterPlan, VoiceRender, AudioCompose, and AudioExport. It also lists VoiceRender engines and their installation, status, version, and compatibility information. Install a missing engine through VoiceRender, for example `python -m pip install "voicerender[pocket]"`.

## Removed commands

Version 0.5.0 removes Readio's former generic project, planning, rendering, synthesis, composition, export, engine/catalog, configuration, verification, and Spotify commands. Their specialist-owned operations are available from the corresponding package CLIs. Readio does not provide compatibility aliases or a migration command for old project state.
