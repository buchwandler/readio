# CLI reference

Use `readio --help` and each command's `--help` for installed options. Readio delegates domain behavior and persisted artifacts to SSMDConvert, UtterPlan, RenderPlan, VoiceRender, AudioCompose, and AudioExport.

## Direct speech

```bash
readio speak "Exact literal text."
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello."
readio speak --profile narrator.renderprofile.toml --role narrator \
  --output greeting.wav --no-play "Hello."
```

`speak` synthesizes literal text through VoiceRender and plays by default. `--profile` may be combined with `--role` but not direct `--voice`, `--engine`, or `--target`; `--output` writes WAV and `--no-play` skips playback. Use `--json` for a machine-readable result.

## Control and settings

- `engines` and `models` delegate discovery and management to VoiceRender.
- `voices` discovers voices; `voice source`, `voice clone`, and `voice design` create or inspect specialist artifacts.
- `profile` creates, validates, refreshes, and manages RenderProfile logical roles.

```bash
readio engines list
readio models list --engine kokoro --available
readio voices list
readio profile validate narrator.renderprofile.toml
readio profile role list narrator.renderprofile.toml
readio config init --output readio.toml
readio config validate readio.toml
```

Settings are read only when explicitly selected with `--config PATH`. Precedence is specialist defaults < settings file < CLI flags. Readio does not auto-discover `readio.toml`.

## Single-document render

```bash
readio render chapter.docx --profile narrator.renderprofile.toml \
  --output chapter.m4a --keep-artifacts
readio render article.pdf --profile narrator.renderprofile.toml \
  --config readio.toml --stop-after synth --json
```

The path delegates conversion, planning, RenderPlan projection, preflight, synthesis, composition, and export to specialist APIs. `--stop-after` accepts `convert`, `plan`, `render-plan`, `synth`, `compose`, or `export`; use `--speech SELECTOR` for a test selection. Readio forwards specialist language, conversion/planner configuration, render emphasis/stale-output controls, composition policy, export format/bitrate/metadata/cover, and FFmpeg paths.

Stopping after `synth` with `defer-to-compose` warns that deferred emphasis may not be realized until composition.

`--artifacts DIR` retains files in that directory; `--keep-artifacts` uses `<source-stem>.render`. Otherwise a temporary directory is removed at completion. `--clean-artifacts` is limited to the expected default directory and known artifacts; unknown entries and arbitrary custom paths are refused. Progress is written to stderr, separate from JSON stdout. Consult SSMDConvert for its supported document formats.

## Audiobooks

```bash
readio audiobook convert novel.epub --output novel.ssmdbook \
  --chapters 5-17 --lang en-US --metadata title=Novel \
  --sequence-fallback-mode preserve --speech-mode freeze --spacy off

readio audiobook build novel.ssmdbook --profile narrator.renderprofile.toml \
  --chapters 5-17 --language en-US --diagnostics warnings \
  --speech all --cache-dir ~/.cache/voicerender \
  --target-lufs -18 --bitrate 192k --output novel.m4b --json
```

Conversion options are SSMDConvert-native. Build options cover UtterPlan diagnostics/language, VoiceRender selection and roots/cache/stale-output behavior, AudioCompose roots and output policy, and AudioExport profile/metadata/cover/bitrate/sample rate/channels/FFmpeg options. `--stop-after` accepts `compile`, `render-plan`, `synth`, `compose`, or `export`; `--no-*` flags skip stages. If RenderPlan projection is skipped, use the exact existing variant. UtterPlan's released API accepts native `PlannerConfig` objects but offers no public path loader, so there is no `--planner-config PATH` flag.

## Specialist workflow

For detailed controls, use specialist CLIs and pass UtterPlan's exact returned variant unchanged:

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

`readio doctor --json` reports specialist versions and VoiceRender engine availability. Install backend extras through VoiceRender.
