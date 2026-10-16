# CLI reference

Readio 0.5 exposes `speak`, `audiobook convert`, `audiobook build`, and `doctor`:

```bash
readio --help
readio speak --help
readio audiobook --help
readio audiobook convert --help
readio audiobook build --help
readio doctor --help
```

## Direct speech

`speak` joins positional arguments with spaces, sends the literal text to VoiceRender, and plays the returned audio through the current device.

```bash
readio speak "Hello from Readio."
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello from Readio."
readio speak --engine inflect --target micro-v2 --voice default --lang en "Hello."
```

Use `--voice` for a VoiceRender selector, or supply `--engine` and `--target` explicitly. `--lang` defaults to `en-US`; `--speaker`, `--offline`, `--refresh`, and repeatable `--engine-option KEY=VALUE` are also available. Backend extras are installed through VoiceRender, for example `python -m pip install "voicerender[kokoro]"`.

## Audiobook conversion and build

```bash
readio audiobook convert novel.epub --output novel.ssmdbook --chapters 5-17
readio audiobook build novel.ssmdbook --profile narration.renderprofile.toml \
  --chapters 5-17 --output novel.m4b --json
```

Conversion and workspace structure belong to SSMDConvert. The build calls UtterPlan compilation and RenderPlan projection, VoiceRender synthesis, AudioCompose composition, and AudioExport. Options include `--chapters`, `--profile`, `--variant`, `--force`, `--fail-fast`, `--stop-after`, `--output`, `--export-profile`, `--cover`, and `--bitrate`. `--stop-after` accepts `compile`, `render-plan`, `synth`, `compose`, or `export`.

JSON output includes the workspace path, returned variant, and names of stages whose specialist results were returned. Readio does not interpret specialist report schemas or persist a parallel stage status.

## Manual specialist workflow

Use the specialist CLIs for package-specific controls. Keep the exact variant returned by UtterPlan unchanged:

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

## Diagnostics and removed commands

```bash
readio doctor
readio doctor --json
```

Doctor reports the installed specialist package versions and VoiceRender engine availability. Install missing engines through VoiceRender.

Version 0.5.0 removes the former generic project, planning, rendering, synthesis, composition, export, engine/catalog, configuration, verification, and Spotify commands. There are no compatibility aliases or migration commands for old project state.
