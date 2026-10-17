# CLI reference

Readio exposes a thin convenience and control plane. Specialist packages remain authoritative for schemas, validation, persisted artifacts, and detailed reports.

```bash
readio --help
readio engines --help
readio models --help
readio voices --help
readio voice --help
readio profile --help
readio config --help
readio speak --help
readio render --help
readio audiobook convert --help
readio audiobook build --help
readio doctor --help
```

## Engine, voice, and profile controls

These commands delegate to VoiceRender:

- `readio engines list|show|options`
- `readio models list|show|install|remove|verify`
- `readio voices list|show`
- `readio voice source create|show|verify`, `readio voice clone`, and `readio voice design`
- `readio profile create|show|validate|refresh`, plus `profile role list|set|remove` and `profile default-role`

Use each subcommand's `--help` for required VoiceRender selectors and options. A profile can bind multiple logical roles; `speak --profile PATH --role NAME` selects one without changing its saved engine/voice identity.

## Direct speech

```bash
readio speak "Hello from Readio."
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello."
readio speak --profile narrator.renderprofile.toml --role narrator \
  --output hello.wav --no-play --json "Hello."
```

`speak` passes literal text directly to VoiceRender and plays by default. Use `--output PATH` to save WAV audio, `--no-play` to skip playback, and `--json` for a machine-readable result. `--profile` is mutually exclusive with direct `--voice`, `--engine`, and `--target` selection; `--role` requires `--profile`.

## One-document rendering

```bash
readio render chapter.docx --profile narrator.renderprofile.toml \
  --output chapter.m4a --keep-artifacts

readio render article.pdf --profile narrator.renderprofile.toml \
  --config readio.toml --stop-after render-plan --json
```

The stages are SSMDConvert conversion, UtterPlan planning, RenderPlan projection, VoiceRender preflight/synthesis, AudioCompose, and AudioExport. `--stop-after` accepts `convert`, `plan`, `render-plan`, `synth`, `compose`, or `export`. Use `--speech SELECTOR` for a test render and `--cache-dir`, `--reset-stale`, `--force`, and `--emphasis-policy` for supported VoiceRender controls.

Stopping after `synth` while using `defer-to-compose` prints a warning that deferred emphasis may not be realized before composition.

`--artifacts DIR` retains intermediate files in the selected directory. `--keep-artifacts` retains them in `<source-stem>.render`; without either option Readio uses a temporary directory and removes it when the command finishes. `--clean-artifacts` only accepts that expected default directory and removes only recognized generated artifacts; unknown entries, symlinks, and custom directories are refused. On failure, completed outputs remain in retained directories. Readio does not create project state or a competing RenderPlan/manifest.

`--config PATH` is explicit: Readio does not auto-discover a `readio.toml`. Specialist defaults are overridden by settings, then by command-line flags. Create and validate a template with:

```bash
readio config init --output readio.toml
readio config show readio.toml
readio config validate readio.toml
```

The settings sections cover document language/conversion/planning, render emphasis and stale-output behavior, AudioCompose policy, and export format/bitrate. For document rendering, `--export-sample-rate` and `--export-channels` configure AudioCompose's output WAV because AudioExport encodes that WAV; AudioCompose validates supported values. Other specialist-specific options are forwarded rather than reimplemented.

Progress is written to stderr. `--json` writes only the result or structured error to stdout; `--no-progress` disables progress callbacks.

## Audiobook conversion

```bash
readio audiobook convert novel.epub --output novel.ssmdbook \
  --chapters 5-17 --lang en-US --metadata title=Novel \
  --sequence-fallback-mode preserve --speech-mode freeze --spacy off
```

Conversion options are forwarded through SSMDConvert's `BookConversionOptions` and `SpeechOptions`; the package validates selectors and policies. `--no-progress` sends no progress callback. The default output replaces the source suffix with `.ssmdbook`.

## Audiobook build

```bash
readio audiobook build novel.ssmdbook --profile narrator.renderprofile.toml \
  --chapters 5-17 --language en-US --diagnostics warnings \
  --speech all --cache-dir ~/.cache/voicerender \
  --target-lufs -18 --bitrate 192k --output novel.m4b
```

The build forwards:

- Planning: `--language`, `--diagnostics errors|warnings|all|none`.
- VoiceRender: `--speech`, `--renderplan-root`, `--voicerender-output-root`, `--cache-dir`, `--reset-stale`, and `--render-emphasis-policy`.
- AudioCompose: specialist roots, `--media-root`, `--save-audiojobs`, sample rate/channels, clipping, loudness, peak, and emphasis policies.
- AudioExport: `--output`, `--export-profile`, repeatable `--metadata KEY=VALUE`, `--cover`, `--bitrate`, sample rate/channels, `--ffmpeg`, and `--ffprobe`.

`--stop-after` accepts `compile`, `render-plan`, `synth`, `compose`, or `export`. `--no-compile`, `--no-render-plan`, `--no-synth`, `--no-compose`, and `--no-export` skip stages; when projection is skipped, pass the exact existing `--variant`. `--force` and `--fail-fast` are forwarded where supported. UtterPlan exposes a native `PlannerConfig` object but no public path loader, so Readio does not offer a `--planner-config PATH` bridge; Python callers can provide a native `BookCompileRequest`.

The result reports the exact variant and which specialist result objects were returned. Readio does not interpret specialist reports or persist parallel stage status.

## Specialist workflow and diagnostics

For detailed package-specific controls, use the specialist CLIs and preserve UtterPlan's exact variant:

```bash
ssmdconvert book convert novel.epub -o novel.ssmdbook
utterplan compile-book novel.ssmdbook
voicerender profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narrator.renderprofile.toml
utterplan render-book novel.ssmdbook --profile narrator.renderprofile.toml

VARIANT="<exact variant returned by utterplan render-book>"
voicerender render-book novel.ssmdbook --variant "$VARIANT"
audiocompose compose-book novel.ssmdbook --variant "$VARIANT"
audioexport export-book novel.ssmdbook --variant "$VARIANT" --output novel.m4b
```

```bash
readio doctor
readio doctor --json
```

Doctor reports specialist package versions and VoiceRender engine availability. Install backend extras through VoiceRender, for example `python -m pip install "voicerender[kokoro]"`.
