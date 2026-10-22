# CLI reference

Readio's lifecycle commands live under `readio bundle`. Every workflow operates on an editable `.ssmd-bundle` directory; the manifest determines its content kind and specialists dispatch accordingly. Use `readio --help`, `readio bundle --help`, and command-specific help for the installed options.

```bash
readio --help
readio bundle --help
readio bundle convert --help
readio bundle compile --help
readio bundle run --help
readio speak --help
readio doctor --help
```

## Bundle lifecycle

### Convert and compile

```bash
readio bundle convert article.docx -o article.ssmd-bundle
readio bundle compile article.ssmd-bundle --language en-US
readio bundle convert novel.epub -o novel.ssmd-bundle
readio bundle compile novel.ssmd-bundle --language en-US --chapters 5-17
```

### Read-only bundle status

Use status before resuming an interrupted workflow. It validates the editable bundle and reports specialist-owned compile, RenderPlan, render, compose, and optional export state, with the first safe next command when known:

```bash
readio bundle status novel.ssmd-bundle
readio bundle status novel.ssmd-bundle --profile narrator.renderprofile.toml --chapters 5-17
readio bundle status novel.ssmd-bundle --variant 'exact-variant' --json
```

Inspection is read-only. Incomplete work returns success; invalid bundles or failed inspection return an error. A missing specialist inspection capability is reported as `unknown` rather than inferred from filenames.
`bundle convert` delegates source conversion and output handling to SSMDConvert. Readio forwards the source, optional output path, chapters, language, and force option; it does not choose the bundle kind or construct an output suffix. `bundle compile` delegates semantic compilation to UtterPlan. Planner options include language, diagnostics, text preparation, overlap/renderability policy, and optional spaCy controls.

### RenderPlan projection

```bash
readio bundle render-plan article.ssmd-bundle \
  --profile narrator.renderprofile.toml --json
```

`render-plan` delegates profile-specific projection to UtterPlan and reports the specialist's exact variant. Preserve that value unchanged when running stages manually.

### Render, compose, and export

```bash
VARIANT="<exact variant returned by render-plan>"
readio bundle render article.ssmd-bundle --variant "$VARIANT"
readio bundle compose article.ssmd-bundle --variant "$VARIANT" \
  --save-audiojobs audiojobs.json
readio bundle export article.ssmd-bundle --variant "$VARIANT" \
  --output article.m4a
```

`render` delegates to VoiceRender, `compose` to AudioCompose, and `export` to optional AudioExport. Use `--save-audiojobs` to request AudioCompose job output. The export command exposes the kind-neutral `ExportRequest` options, including output/profile, format, timeline, sample rate, channels, metadata, cover, roots, encoder paths, and force behavior. AudioExport validates kind-sensitive option combinations. Install the optional integration with `python -m pip install "readio[audioexport]"`.

### Run the complete pipeline

```bash
readio bundle run article.docx \
  --bundle-output article.ssmd-bundle \
  --profile narrator.renderprofile.toml \
  --language en-US --json

readio bundle run novel.ssmd-bundle \
  --profile novel.ssmd-bundle/narrator.renderprofile.toml \
  --chapters 5-17 --language en-US --export --output novel.m4b
```

`bundle run` accepts source material or an existing bundle. It converts source material when needed, then compiles, projects the RenderPlan, renders, and composes. `--export` adds the optional AudioExport stage. Readio forwards UtterPlan's projected variant unchanged across downstream calls. `--bundle-output` selects the output path for source conversion. The command does not accept a caller-selected kind; SSMDConvert and the bundle manifest own kind detection.

Stage-specific roots, policies, force flags, chapter selection, metadata, and encoder paths are forwarded to specialist APIs rather than reimplemented. Use `--no-progress` to disable callbacks. Progress is written to stderr, separately from JSON results on stdout.

## Voice and profile management

These command groups delegate to VoiceRender:

- `engines` and `models` discover, inspect, install, verify, and remove engine models.
- `voices` discovers voices; `voice source` creates and inspects voice-source artifacts; `voice clone` creates a specialist RenderProfile.
- `profile` creates, inspects, validates, refreshes, and manages RenderProfile roles.

Use command-specific help for selectors and options. Install engine backends through VoiceRender extras, for example `python -m pip install "voicerender[kokoro]"`.

## Direct speech

```bash
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello."
readio speak --profile narrator.renderprofile.toml --role narrator \
  --output greeting.wav --no-play "Hello."
```

`speak` synthesizes literal text through VoiceRender and plays by default. `--output` writes WAV and `--no-play` skips local playback. Use `--json` for a machine-readable result.

## ONNX Runtime provider controls

```bash
readio runtime status
readio runtime status --json
readio runtime list
readio runtime list --json
readio runtime use cpu
readio runtime use cuda
readio runtime use auto
readio runtime use cuda,cpu
readio runtime reset
```

`runtime status` distinguishes the imported ONNX Runtime distribution/build from its available providers, configured policy, source, and providers resolved for new sessions. `runtime list` lists detected distributions and providers reported by the imported build; its `SELECTED` column is not an active-session report. Provider availability is determined by the imported build. `runtime use` saves a default policy for future compatible sessions and does not install, uninstall, or switch runtime distributions. Environment variables such as `ONNXVOICE_PROVIDER` override saved configuration; `runtime reset` removes the saved preference but does not unset environment variables. An explicit `--engine-option providers=...` or provider set in a RenderProfile overrides the global default. Runtime package/distribution changes are performed outside Readio.

## Dependency diagnostics

```bash
readio doctor
readio doctor --json
```

Doctor reports specialist package versions, VoiceRender engine availability, and compact ONNX Runtime distribution and provider-policy status. A missing or failed runtime is shown with a concise diagnostic instead of crashing the report.

## Verbose diagnostics and timing

Global `-v`/`--verbose` and `-vv` flags are accepted before or after nested commands. `-v` emits INFO diagnostics and elapsed command/stage timings; `-vv` adds DEBUG dispatch and inspection details plus tracebacks for unexpected errors. Diagnostic output is sent to stderr, leaving normal results and JSON on stdout.

```bash
readio -v bundle status novel.ssmd-bundle
readio bundle status novel.ssmd-bundle -vv --json
readio -vv bundle run novel.ssmd-bundle --profile narrator.renderprofile.toml --no-progress
```

`--no-progress` suppresses progress callbacks only; verbose diagnostics and timing remain enabled. Timings use a monotonic clock and are not persisted.
