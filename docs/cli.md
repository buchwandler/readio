# CLI reference: project pipeline

## Getting help

```bash
readio
readio --help
readio render --help
readio audiobook --help
```

Running `readio` without arguments prints the root command overview. Use `-h` or `--help` on the root command and on individual commands.

Command groups show their child-command help when invoked without a child. `readio plan` is the intentional exception: it builds a plan for the current project.

Use `readio engines` to list known synthesis engines and `readio formats` to list generic audio and audiobook output formats. Both commands support `--json`.

## Project lifecycle

```text
readio project init SOURCE -o PROJECT
readio plan                         # build current project
readio plan build [PROJECT]
readio plan roles [PROJECT]
readio plan bind ROLE VOICE [PROJECT] [--engine ENGINE] [--provider PROVIDER] [--project PROJECT]
readio plan unbind ROLE [--project PROJECT]
readio synth PROJECT [--engine ENGINE] [--voice VOICE] [--select SELECTOR]
readio preview PROJECT --select SELECTOR [--voice VOICE] [-o PREVIEW.wav]
readio compose PROJECT [--target-lufs FLOAT] [--progress | --no-progress] [--json]
readio export PROJECT --format {wav,flac,mp3,m4a,ogg,opus} [--bitrate BITRATE] [--force]
readio audiobook export PROJECT --format m4b [--title TITLE] [--author AUTHOR] [--cover IMAGE] [--bitrate BITRATE] [--force]
readio status PROJECT [--json]
readio render PROJECT --format FORMAT
```

`readio plan` manages persistent project semantics and roles. Running it without a subcommand builds the project in the current directory; it does not inspect one-shot text or choose an engine. Use `readio render --dry-run` for one-shot execution planning:

```bash
readio render --file episode.ssmd --format mp3 --dry-run --json
```

## Voice role targets and mixed-engine routing

### Global role bindings

Use `readio roles` for persistent user-global role targets:

```bash
readio roles bind host kokoro:v1.0/af_sarah
readio roles bind guest en_US-amy-medium --engine piper
readio roles list --json
readio roles unbind guest
```

New global targets are saved under the top-level `[roles.<role>]` configuration table and take precedence over legacy `[voices.<provider>.roles]` values. Legacy values remain readable for roles without a new target; conflicting legacy definitions for such a role are ambiguous rather than implicitly assigned to one provider.

### Project role bindings

Project bindings use the same structured target model. Bind semantic references directly; Readio retains the resolved engine, canonical voice, and target ID without selector provenance:

```bash
readio plan bind host kokoro:v1.0/af_sarah
readio plan bind guest piper:en_US-amy-medium
readio plan roles
readio synth
```

New bindings are stored under `settings.ssmd.role_bindings.<role>` in `project.json`. A binding does not select a project-wide provider or engine. Role inspection reports the engine and derived provider per target. `readio plan roles --provider PROVIDER` filters results; it does not override project bindings.

Run these commands from the project root or a nested directory. An explicit project path can be supplied positionally or through `--project`; supplying conflicting paths is an error. For a native voice ID that does not identify its engine, pass `--engine`, for example `readio plan bind guest en_US-amy-medium --engine piper`. Use a semantic reference when a target must be explicit. `--provider` is accepted for compatibility, must agree with the target engine, and does not choose a project-wide route.

Legacy manifests using `settings.ssmd.voice_bindings.<provider>.<role>` remain readable. The optional `settings.ssmd.voice_provider` scopes those legacy bindings when present; it does not control new role-centric bindings. Without an active legacy provider, conflicting definitions for the same role are ambiguous. SSMD document `voice_bindings` syntax is unchanged, and a role bound in multiple provider namespaces is ambiguous. New `plan bind` writes role-centric targets without rewriting unrelated legacy settings. There is no automatic migration command.

Resolution precedence is document binding, invocation `--voice-bind`, project role target, global configured role, then a direct semantic reference or context-resolved native voice ID. The semantic plan remains independent of casting. Project synthesis routes each bound segment through its target engine and uses the normal project synthesis selection for unbound segments, opening reusable sessions per distinct route.

## Saved project pipeline settings

Inspect, patch, or clear supported settings with the project command family:

```text
readio project settings [--project PROJECT] [--json]
readio project settings show [PROJECT] [--json]
readio project settings set [PROJECT] [--engine ENGINE] [--model MODEL] [--language LANG] [--voice VOICE] [--speed FLOAT]
  [--mastering PROFILE] [--target-lufs FLOAT] [--sample-rate HZ]
  [--export-format FORMAT] [--export-output PATH] [--export-bitrate RATE]
  [--audiobook-output PATH] [--audiobook-title TITLE] [--audiobook-author AUTHOR]
  [--audiobook-cover IMAGE] [--audiobook-bitrate RATE]
readio project settings clear [PROJECT] --section {synthesis,composition,export,audiobook_export}
```

`set` updates only sections represented by its flags and preserves other saved section fields. It exposes named supported values, not arbitrary JSON editing. Relative paths are interpreted from the project root. Invocation-only `--force` and `--refresh` flags are never persisted.

Synthesis, composition, generic export, and audiobook export defaults are used by requestless project APIs and builds. Explicit API or stage options override saved values for that invocation only. `readio status` reports stage-specific staleness when saved settings differ from built provenance; synthesis caches and previous outputs are retained.

### Shared speech controls

The `--speed` option and `reader.speed` configuration value are engine synthesis multipliers. PyKokoro receives speed directly, PiperSynth maps it to `length_scale = 1 / speed`, and PocketSynth accepts only `1.0`; unsupported explicit values fail before inference. Composition rate is separate and is not also changed by `--speed`.

Use `--voice-level off|calibrated` or `reader.voice_level` to select voice-level handling. The resolved voice-level mode and synthesis speed are included in speech-cache identity.

PocketSynth project runs select a bundle and either a predefined voice or a reference WAV. The reference asset is represented by its SHA-256 content identity in the resolved render plan:

```bash
readio synth PROJECT --engine pocket --model BUNDLE_ID --voice VOICE --precision int8
readio synth PROJECT --engine pocket --model BUNDLE_ID --voice-file reference.wav --temperature 0.6
```

## Voice catalog filters

For `readio voices list`, registered engine names passed as `--model` are shortcuts only when `--engine` is omitted: `piper` and `pipersynth` select Piper, `pykokoro` and `kokoro` select PyKokoro, and `pocket` selects PocketSynth. The JSON `filters` object reports the effective engine and clears the model field for shortcuts. Concrete model IDs, voice bundles, and Pocket bundle IDs remain model filters.

Voice references use `SYSTEM:TARGET[/VOICE]`, such as `kokoro:v1.0/af_heart`, `piper:en_US-amy-medium`, or `pocket:english_2026-04/alba`. Voice listing filters (`--engine`, `--model`, `--lang`, and `--gender`) select descriptive metadata, not voice identity. Native IDs are accepted when discovery context resolves them uniquely; use a semantic reference when the target must be explicit.
Pocket language filtering treats a generic bundle language as compatible with a specific query: `--lang en-us` includes a bundle advertising `en`, but excludes one explicitly advertising `en-GB`. The generic voice remains labeled `en`; Readio does not infer a regional locale.

```bash
readio voices list --model piper --lang en-us
readio voices list --engine piper --model en_US-amy-medium
```

Project initialization converts supported filesystem document inputs through ssmdconvert. Ordinary document inputs include text/Markdown, HTML, PDF, DOCX, EPUB, and SSMD. The original file is retained under `source/` as provenance; the editable semantic input is canonical SSMD under `document/document.ssmd.md`. Planning reads the persisted SSMD, not the original source. Editing it replans changed semantics. If the source snapshot changes, status reports the provenance change but does not silently reconvert; initialize a new project from the updated source to ingest it. `readio project init novel.epub` creates one combined document project. `.ssmdbook` bundles are multi-chapter audiobook inputs and are rejected by generic project initialization.

## Book-source audiobook ingestion

```text
readio audiobook chapters SOURCE [--json]
readio audiobook init SOURCE [--chapters SPEC] [-o PROJECT] [--json]
```

`SOURCE` may be an EPUB file, an `.ssmdbook` directory, or an `.ssmdbook.zip` file created by ssmdconvert. `chapters` reports metadata and selectable chapter source numbers. `init` defaults to all chapters and otherwise accepts comma-separated numbers and inclusive ranges such as `2-4,7`. Selectors address the available 1-based source chapter numbers, including non-contiguous numbers in bundle subsets, and selected chapters retain source order. The selection is persisted in the project, so downstream commands need no book-specific option.

Book inspection and conversion use ssmdconvert's public API. Readio persists each selected standalone chapter as editable SSMD under `document/chapters/`; the original book source is retained as provenance. Editing chapter SSMD replans affected content without re-extracting the source. A changed source snapshot is reported, but it does not overwrite or invalidate persisted semantic SSMD. Reinitialize into a new project from the updated source to ingest changed content.

Export the composed audiobook master with embedded chapter metadata using the audiobook-only command:

```bash
readio audiobook export PROJECT --format m4b --output book.m4b \
  --title "Optional title" --author "Optional author" --cover cover.jpg --bitrate 96k
```

Title and author default from the book metadata. `--cover` is optional and accepts an explicit JPEG/PNG file; automatic source cover extraction is not part of the current ssmdconvert integration, so provide a cover explicitly. M4B uses AAC with a Readio default bitrate of 192k. Existing unrelated output files require `--force`; unchanged Readio-owned outputs are safely reusable/replaced. Generic `readio export` supports FLAC and Opus; `.ogg` continues to mean Ogg/Vorbis.

## Persistent project status

`readio status` discovers the project by walking upward from the current directory. Human output shows the project root, source format, each stage state/reason, dependency blocking, and the primary next command. Structured output preserves stable reason codes and includes `issues` plus `next_actions`.

Plan artifact reasons include `plan.index.missing`, `plan.index.invalid`, `plan.artifact.missing`, `plan.artifact.hash_mismatch`, `plan.artifact.plan_id_mismatch`, and `plan.stale.document_format_mismatch`. A wrong-format legacy plan is therefore rebuilt with `readio plan` instead of being silently reused.

## Synthesis progress and JSON

Project `synth` and `preview` accept the shared `--progress` / `--no-progress` option. Interactive progress is written to stderr and includes the resolved profile, cache counts, model-loading phase, and unit/segment preview. `-v` and `-vv` select the existing INFO and DEBUG logging levels; use them for bounded stage, runtime, timing, and cache diagnostics.

`readio synth --json` emits one final JSON object on stdout. It includes the project, scope, plan ID, profile identity and engine/model/voice/language, selector/count, and cache reuse/render counts. Progress and logs remain on stderr, and automatic progress is disabled for JSON unless explicitly forced.

## Mastering profiles

Composition, preview, and bounded `render` default to `--mastering spoken-word` (`-16 LUFS`, `-1 dBTP`). Select `spoken-word-dual-mono` (`-19/-1`), `broadcast-ebu` (`-23/-1`), `peak-safe` (no LUFS target, `-1 dBTP`), or `off` (no target or ceiling). Readio does not offer an ACX LUFS preset: ACX compliance requires separate RMS, peak, and noise-floor checks.

`--target-lufs` and `--true-peak-ceiling-dbtp` are expert numeric overrides; omitted values inherit from the selected profile. Choose `peak-safe` or `off` to disable inherited processing. `--peak-policy reduce_gain` preserves transparent constant-gain behavior and reduces requested gain when needed to meet the true-peak ceiling; it is not a limiter. Use `--peak-policy error` to fail instead of reducing gain.

Human output and `--json` include the selected profile, before/after integrated loudness, sample and true peaks, requested/applied gain, target status, and warnings. Progress separates assembly and loudness finalization (analysis, gain, and cached post-gain metrics) from AudioJob, WAV, timeline, hashing, and state writes. Runtime timings are diagnostic only and never enter composition identity.

## Composition progress

`readio compose PROJECT` shares the progress policy with synthesis, preview, render, and project rendering. Progress is enabled automatically on an interactive terminal, can be forced with `--progress`, and can be disabled with `--no-progress`. All progress is written to stderr.

Composition events identify the current speech segment and operation, show completed and total segments, and show an approximate ETA only after enough segment processing has completed. The ETA covers segment processing and does not predict assembly, complete-output loudness or true-peak processing, or artifact writing. Those stages are rendered separately so all segments reaching 100 percent does not imply that the master is complete.

With `--json`, stdout remains one JSON document. Explicit progress remains on stderr, and progress callbacks are runtime observations only. They do not enter composition IDs, AudioJob serialization, timelines, or composition state identities.

`readio plan` is a project command family: `build` creates semantic Utterplan artifacts, `roles` inspects SSMD roles, and `bind` / `unbind` manage project-local acoustic settings. It never selects an engine or loads TTS. Use `readio render --dry-run` to inspect the complete execution plan for one-shot input.
