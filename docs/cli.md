# CLI reference

Readio's primary workflow surface is centered on `render`, `status`, and `speak`. Specialist CLIs remain available when developers deliberately need direct stage-level control; Readio does not duplicate those stage commands.

```bash
readio --help
readio intent --help
readio render --help
readio status --help
readio speak --help
readio doctor --help
```

## Unified render workflow

A normal render uses a run-scoped temporary workspace. Supply `--bundle PATH` only when the editable SSMD bundle should persist. Passing an existing `.ssmd-bundle` as `SOURCE` also selects persistent mode. Readio never invents a default persistent bundle path or hidden `.readio/` workspace.

```bash
# One-shot render; intermediate workspace is temporary and cleaned afterward.
readio render article.docx --profile narrator.renderprofile.toml --language en-US

# Persistent editable workspace. Initial conversion materializes the full book.
readio render novel.epub --bundle novel.ssmd-bundle \
  --profile narrator.renderprofile.toml --language en-US --chapters 5-17

# Inspect the planned work without executing it.
readio render --config novel.readio.toml --dry-run
readio status novel.ssmd-bundle --profile narrator.renderprofile.toml --chapters 5-17
readio status --config novel.readio.toml
```

`status` and `render --dry-run` use the same read-only workflow inspection. They report workspace mode, selected scope, output, and specialist-backed state/action for each workflow step. Inspection does not create a temporary workspace or mutate artifacts.

`--rebuild-from STAGE` explicitly rebuilds a stage and downstream derived state; choices are `convert`, `compile`, `render-plan`, `render`, `compose`, and `export`. Rebuilding from `convert` while targeting a persistent bundle is explicit consent to reconvert. `--replace-output` separately allows replacing an existing final output; it is not implied by a rebuild. Unknown or unsafe specialist state fails closed rather than triggering broad deletion.

Progress and diagnostics go to stderr; `--json` keeps the structured result on stdout. `--no-progress` suppresses progress callbacks only. AudioExport is included in the core installation; `--ffmpeg PATH` and `--ffprobe PATH` select tool executables.

## Render intents

A render intent is reviewable, user-owned TOML. The current schema is `readio.render.v2`; `[input]` describes source content, while workspace mode and persistent bundle path belong under `[workspace]`.

```toml
schema = "readio.render.v2"
kind = "book"

[input]
source = "novel.epub"
chapters = "5-17"
language = "en-US"

[workspace]
mode = "bundle"
bundle = "novel.ssmd-bundle"

[voice]
profile = "narrator.renderprofile.toml"
```

Use `mode = "temporary"` when intermediate state should be discarded after the run. Persistent mode is selected explicitly with a bundle path or by using an existing bundle as the source. On first creation, a persistent book bundle contains the complete source inventory; `input.chapters` scopes downstream planning and rendering, not initial conversion.

```bash
readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narrator.renderprofile.toml
readio intent document create article.docx \
  --profile narrator.renderprofile.toml --language en-US
readio intent book create novel.epub \
  --profile narrator.renderprofile.toml --language en-US --chapters 5-17 \
  --bundle novel.ssmd-bundle
readio intent show novel.readio.toml --resolved
readio intent validate novel.readio.toml
readio render --config novel.readio.toml --dry-run
readio render --config novel.readio.toml
```

Intent files default to `<source-stem>.readio.toml`; `--output-config`, `--stdout`, `--minimal`, and `--force` control persistence. `intent show --toml` prints normalized TOML; `--resolved` displays config-relative paths and inferred values; `--json` emits structured output. Book intents default to M4B and accept `--chapters`; document intents default to MP3. A VoiceRender RenderProfile remains a separate, user-owned artifact. CLI values override saved intent values.

For a one-off render without saving an intent, provide `SOURCE` and `--profile`. Use `--bundle` to retain the editable workspace; omit it for temporary execution. Existing final outputs require `--replace-output` before replacement.

## Manual specialist workflows

For package-specific stage controls, invoke the specialist CLIs directly. Their APIs own stage schemas, validation, persisted artifacts, and reports. Readio's workflow remains the recommended user-facing file-to-audio path.

## Voice and profile management

These command groups delegate to VoiceRender:

- `engines` and `models` discover, inspect, install, verify, and remove engine models.
- `voices` discovers voices; `voice source` creates and inspects voice-source artifacts; `voice clone` creates a specialist RenderProfile.
- `profile` creates, inspects, validates, refreshes, and manages RenderProfile roles.

Use command-specific help for selectors and options. Install engine backends through VoiceRender extras, for example `python -m pip install "voicerender[kokoro]"`.

### Source-aware profiles for SSMD documents

`profile document` inspects explicit voice-role blocks in an existing `.ssmd.md` source using UtterPlan. Inspection does not infer speakers from prose or download language/engine models. Roles appear in source order, with `narrator` first when present; the block counts are useful for reviewing a cast:

```bash
readio profile document inspect SOURCE [--json]
```

Create a complete native VoiceRender RenderProfile in one command:

```text
readio profile document create SOURCE
  --engine ENGINE --target TARGET --language LANGUAGE
  [--voice ROLE=VOICE]...
  [--same-as ROLE=ROLE]...
  [--speaker ROLE=SPEAKER]...
  [--option ROLE:KEY=VALUE]...
  [--default-role ROLE] [--offline] [--refresh]
  -o OUTPUT [--force] [--json]
```

Common engine, target, and language values are required; Readio does not guess them. A bare `--voice ROLE=VOICE` inherits the command engine and target. A full VoiceRender selector such as `--voice narrator=kokoro:v1.0/af_heart` supplies that role's engine and target and overrides those defaults. `--speaker` is mutually exclusive with `--voice` for a role. `--option` repeats for role-specific engine options.

Every discovered role needs a direct voice/speaker binding or an explicit alias. For example, four logical roles can share three physical voices:

```bash
readio profile document create meeting.ssmd.md \
  --engine kokoro --target v1.0 --language en-US \
  --voice narrator=af_heart --voice host=af_sarah \
  --voice guest=am_michael --same-as analyst=guest \
  --default-role narrator -o meeting.renderprofile.toml
```

`--same-as analyst=guest` copies the complete `guest` binding while preserving `analyst` as a separate logical role. Missing, duplicate, unknown, conflicting, or cyclic assignments fail before the final profile is written. Existing output is protected unless `--force` is supplied. Profile serialization and validation remain VoiceRender-owned.

Inspect output can be JSON for tools; it includes `source`, `roles` with block counts, and `default_role_candidate`. Create JSON additionally reports `output`, `discovered_roles`, `bound_roles`, `aliases`, `default_role`, and the VoiceRender `profile_id` when available.

Keep the two artifacts separate: review the generated `*.renderprofile.toml`, then create an intent with `readio intent document create SOURCE --profile PROFILE`. Intent creation without `--profile` gives an actionable next command for SSMD sources, but does not choose voices or create either artifact automatically.

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
readio -v status novel.ssmd-bundle --profile narrator.renderprofile.toml
readio -vv render novel.epub --bundle novel.ssmd-bundle --profile narrator.renderprofile.toml --dry-run
```

`--no-progress` suppresses progress callbacks only; verbose diagnostics and timing remain enabled. Timings use a monotonic clock and are not persisted.
