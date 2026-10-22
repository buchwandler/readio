# CLI reference

The intent-first complete workflow uses `readio intent` to create/review user-owned TOML and `readio render` to execute. `readio bundle` remains the advanced stage-by-stage interface. Readio delegates domain behavior and persisted artifacts to SSMDConvert, UtterPlan, VoiceRender, AudioCompose, and optional AudioExport.

## Intent-first render

Create a separate VoiceRender profile, then create and review a kind-specific Readio intent. The default config is `<source-stem>.readio.toml`:

```bash
readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narrator.renderprofile.toml
readio intent document create article.docx --profile narrator.renderprofile.toml --language en-US
readio intent book create novel.epub --profile narrator.renderprofile.toml --chapters 5-17
readio intent show novel.readio.toml --resolved
readio intent validate novel.readio.toml
```

Book output defaults to M4B and documents to MP3. Intent options include `--output-config`, `--stdout`, `--minimal`, and safe overwrite with `--force`; see kind-specific `create --help` for planning, composition, metadata, and export options. VoiceRender RenderProfile remains a separate configuration, not a Readio cast schema.

```bash
readio render --config novel.readio.toml --dry-run
readio render --config novel.readio.toml
readio render article.docx --profile narrator.renderprofile.toml --language en-US
readio status --config novel.readio.toml
```

`--dry-run` resolves configuration and inspects existing specialist status without executing stages. Existing bundles are reused. Use repeated `--force-stage STAGE` or `--restart-from STAGE` for explicit reruns; existing output requires forcing `export`. `--reconvert` forces conversion, and `--reset-stale-output` is separate. AudioExport is optional (`readio[audioexport]`); selected format, FFmpeg, and FFprobe are preflighted before conversion. `--ffmpeg PATH` and `--ffprobe PATH` select tool overrides. Progress/diagnostics use stderr; `--json` results use stdout. Readio writes no `.readio/` workspace state.

## Advanced bundle lifecycle

```bash
readio bundle convert chapter.docx -o chapter.ssmd-bundle
readio bundle compile chapter.ssmd-bundle --language en-US
readio bundle render-plan chapter.ssmd-bundle --profile narrator.renderprofile.toml
readio bundle run chapter.ssmd-bundle --profile narrator.renderprofile.toml --language en-US
```

All SSMD lifecycle operations use editable `.ssmd-bundle` directories. `convert` delegates source handling and bundle creation to SSMDConvert. `compile` delegates to UtterPlan. `render-plan` projects a profile-specific RenderPlan; when continuing manually, pass its exact variant unchanged:

### Read-only status

Run `readio bundle status BUNDLE` before manually resuming an interrupted workflow. It reports specialist-owned state and the first safe next action, if known, without writing files. An incomplete workflow is a successful status result; unavailable inspection is shown as `unknown` rather than guessed.

```bash
readio bundle status novel.ssmd-bundle --chapters 5-17
readio bundle status novel.ssmd-bundle --variant 'exact-variant' --json
```

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

Global `-v`/`--verbose` and `-vv` flags work before or after nested commands. Diagnostics and monotonic timing go to stderr; JSON and normal results stay on stdout. `--no-progress` suppresses progress callbacks, not verbose diagnostics:

```bash
readio -v bundle status novel.ssmd-bundle
readio bundle status novel.ssmd-bundle -vv --json
readio -vv bundle run novel.ssmd-bundle --profile narrator.renderprofile.toml --no-progress
```
