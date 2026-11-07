# CLI reference

Readio exposes unified `render`, `status`, `speak`, and management commands. Specialist CLIs remain available for package-specific stage controls; Readio does not duplicate their stage-runner commands.

```bash
readio --help
readio intent --help
readio render --help
readio status --help
readio speak --help
readio doctor --help
```

## Workspace policy and rendering

A render uses a run-scoped temporary workspace by default. Supply `--bundle PATH` to keep an editable persistent SSMD bundle, or use an existing `.ssmd-bundle` as the source. Readio never infers persistence from a default filename or creates hidden `.readio/` state.

```bash
readio render article.docx --profile narrator.renderprofile.toml --language en-US
readio render novel.epub --bundle novel.ssmd-bundle \
  --profile narrator.renderprofile.toml --language en-US --chapters 5-17
readio render --config novel.readio.toml --dry-run
readio status novel.ssmd-bundle --profile narrator.renderprofile.toml --chapters 5-17
readio status --config novel.readio.toml
```

`--dry-run` and `status` use the same read-only workflow inspection. Neither creates temporary state or mutates artifacts. `--rebuild-from STAGE` explicitly rebuilds a workflow stage and downstream derived state; `--replace-output` separately authorizes replacing an existing final output. Progress and diagnostics use stderr; JSON results use stdout. `--no-progress` disables only progress callbacks. AudioExport is included in the core installation; `--ffmpeg PATH` and `--ffprobe PATH` select tool executables.

## Render intents

The current schema is `readio.render.v2`. Source and selection belong under `[input]`; workspace mode and bundle path belong under `[workspace]`:

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

Use `mode = "temporary"` for disposable intermediate state. On first creation of a persistent book bundle, Readio materializes the full source inventory; `input.chapters` limits downstream planning and rendering. CLI values override saved intent values.

```bash
readio intent document create article.docx --profile narrator.renderprofile.toml
readio intent book create novel.epub --profile narrator.renderprofile.toml \
  --chapters 5-17 --bundle novel.ssmd-bundle
readio intent show novel.readio.toml --resolved
readio intent validate novel.readio.toml
readio render --config novel.readio.toml
```

Intent files default to `<source-stem>.readio.toml`. `intent show --toml` prints normalized TOML; `--resolved` displays config-relative paths. `--stdout`, `--minimal`, `--output-config`, and `--force` control intent-file persistence. Book intents default to M4B; document intents default to MP3.

## Specialist workflows

For manual stage execution, call the specialist CLIs directly. SSMDConvert owns source conversion and bundle paths, UtterPlan owns planning and projected variants, VoiceRender owns rendering, AudioCompose owns composition, and AudioExport owns encoding. See the individual specialist documentation for their commands and options.

## Voice, profile, and runtime management

`engines`, `models`, `voices`, `voice`, and `profile` commands delegate to VoiceRender. `profile document` can inspect SSMD roles and create a native RenderProfile. Runtime controls (`readio runtime status|list|use|reset`) delegate ONNX Runtime provider policy to VoiceRender. `readio doctor` reports dependency and engine health.

## Diagnostics

Global `-v`/`--verbose` and `-vv` flags are accepted before or after commands. Diagnostics and progress go to stderr, preserving JSON stdout.

```bash
readio -v status novel.ssmd-bundle --profile narrator.renderprofile.toml
readio -vv render novel.epub --bundle novel.ssmd-bundle \
  --profile narrator.renderprofile.toml --dry-run
```
