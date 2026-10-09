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
readio project migrate PROJECT        # explicitly upgrade a v0.3 project
readio plan                         # build current project
readio plan build [PROJECT]
readio plan roles [PROJECT]
readio plan bind ROLE VOICE [PROJECT] [--engine ENGINE] [--project PROJECT]
readio plan unbind ROLE [--project PROJECT]
readio synth PROJECT [--engine ENGINE] [--voice VOICE] [--select SELECTOR]
readio preview PROJECT --select SELECTOR [--voice VOICE] [-o PREVIEW.wav]
readio compose PROJECT [--target-lufs FLOAT] [--progress | --no-progress] [--json]
readio export PROJECT [--profile TOML] [--format {wav,flac,mp3,m4a,ogg,opus}] [--bitrate BITRATE] [--force] [--all --out-dir DIR]
readio audiobook export PROJECT [--profile TOML] [--format m4b] [--title TITLE] [--author AUTHOR] [--cover IMAGE] [--bitrate BITRATE] [--force]
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

Global targets are saved under the top-level `[roles.<role>]` configuration table. Provider-specific `[voices.<provider>.roles]` values are not read by the v0.4 runtime; convert v0.3 configuration explicitly with `readio config migrate`.

### Project role bindings

Project bindings use the same structured target model. Bind semantic references directly; Readio retains the resolved engine, canonical voice, and target ID without selector provenance:

```bash
readio plan bind host kokoro:v1.0/af_sarah
readio plan bind guest piper:en_US-amy-medium
readio plan roles
readio synth
```

New bindings are stored under `settings.ssmd.role_bindings.<role>` in `project.json`. A binding does not select a project-wide provider or engine. Role inspection reports each effective engine-qualified target. `readio plan roles --engine ENGINE` filters results; it does not override project bindings.

Run these commands from the project root or a nested directory. An explicit project path can be supplied positionally or through `--project`; supplying conflicting paths is an error. For a native voice ID that does not identify its engine, pass `--engine`, for example `readio plan bind guest en_US-amy-medium --engine piper`. Use a semantic reference when a target must be explicit. Binding APIs and CLI use `--engine` directly; SSMD's document-level namespace remains part of the external source format.

v0.3 manifests using provider-keyed `settings.ssmd.voice_bindings` are not read by normal runtime code. Run `readio project migrate PROJECT` to create a backed-up schema-3 manifest; conflicts fail for review. SSMD document `voice_bindings` syntax itself remains unchanged, and a role bound in multiple document namespaces is ambiguous.

Resolution precedence is document binding, invocation `--voice-bind`, project role target, global configured role, then a direct semantic reference or context-resolved native voice ID. The semantic plan remains independent of casting. Project synthesis routes each bound segment through its target engine and uses the normal project synthesis selection for unbound segments, opening reusable sessions per distinct route.

## Saved project pipeline settings

Inspect, patch, or clear supported settings with the project command family:

```text
readio project settings [--project PROJECT] [--json]
readio project settings show [PROJECT] [--json]
readio project settings set [PROJECT] [--engine ENGINE] [--model MODEL] [--language LANG] [--voice VOICE] [--voice-file PATH] [--voice-prompt REF] [--speed FLOAT]
  [--mastering PROFILE] [--target-lufs FLOAT] [--sample-rate HZ]
  [--export-format FORMAT] [--export-output PATH] [--export-bitrate RATE] [--export-profile TOML]
  [--audiobook-output PATH] [--audiobook-title TITLE] [--audiobook-author AUTHOR]
  [--audiobook-cover IMAGE] [--audiobook-bitrate RATE] [--audiobook-profile TOML]
readio project settings clear [PROJECT] --section {synthesis,composition,export,audiobook_export}
```

`set` updates only sections represented by its flags and preserves other saved section fields. It exposes named supported values, not arbitrary JSON editing. Relative paths are interpreted from the project root. Invocation-only `--force` and `--refresh` flags are never persisted. AudioExport profile paths may be saved with `--export-profile` and `--audiobook-profile`; direct export options override those defaults only for that operation. The `audioexport` dependency is optional (`pip install 'readio[audioexport]'`). Profiles apply only to file-based project exports; no-profile behavior and `readio speak`'s PCM streaming sink remain unchanged. `status` and project builds compare profile identity so profile-only edits stale only the output stage. M4B profiles use Readio's verified timeline, and foreign profile timelines are rejected.

Synthesis, composition, generic export, and audiobook export defaults are used by requestless project APIs and builds. Explicit API or stage options override saved values for that invocation only. `readio status` reports stage-specific staleness when saved settings differ from built provenance; synthesis caches and previous outputs are retained.

### Shared speech controls

The `--speed` option and `reader.speed` configuration value are engine synthesis multipliers. Kokoro receives speed directly, PiperSynth maps it to `length_scale = 1 / speed`, and PocketSynth accepts only `1.0`; unsupported explicit values fail before inference. Supertonic converts the locale to a supported base language and forwards speed as an engine synthesis multiplier; InflectSynth also supports the shared speed control. Composition rate is separate and is not also changed by `--speed`.

Use `--voice-level off|calibrated` or `reader.voice_level` to select voice-level handling. The resolved voice-level mode and synthesis speed are included in speech-cache identity.

PocketSynth project runs select a bundle and exactly one voice source: `--voice` for a predefined bundle voice, `--voice-file` for a local WAV, or `--voice-prompt` for a managed Kyutai prompt. Local and managed reference sources are pinned by SHA-256 in the resolved render plan; managed prompts also retain the catalog revision and available provenance. Managed prompts apply to the default synthesis voice, not per-role bindings:

```bash
readio synth PROJECT --engine pocket --model BUNDLE_ID --voice VOICE --precision int8
readio synth PROJECT --engine pocket --model BUNDLE_ID --voice-file reference.wav --temperature 0.6
readio voices prompts --engine pocket --dataset alba
readio voices prompts --engine pocket --variant casual --license cc-by-4.0
readio synth PROJECT --engine pocket --model BUNDLE_ID --voice-prompt kyutai-tts-voices:alba-mackenna/casual
```

## Voice catalog filters

For `readio voices list`, canonical engine IDs passed as `--model` are shortcuts only when `--engine` is omitted: `kokoro`, `piper`, `pocket`, `supertonic`, `kitten`, and `inflect`. Input aliases such as `pykokoro`, `pipersynth`, `supertonicsynth`, and `inflectsynth` normalize to the canonical ID. The JSON `filters` object reports the effective engine and clears the model field for shortcuts. Concrete model IDs, voice bundles, and model targets remain model filters.

Voice references use `SYSTEM:TARGET[/VOICE]`, such as `kokoro:v1.0/af_heart`, `piper:en_US-amy-medium`, `pocket:english_2026-04/alba`, `supertonic:supertonic-3/F1`, or `inflect:nano-v2/default`. Voice listing filters (`--engine`, `--model`, `--lang`, and `--gender`) select descriptive metadata, not voice identity. Native IDs are accepted when discovery context resolves them uniquely; use a semantic reference when the target must be explicit.
Pocket language filtering treats a generic bundle language as compatible with a specific query: `--lang en-us` includes a bundle advertising `en`, but excludes one explicitly advertising `en-GB`. The generic voice remains labeled `en`; Readio does not infer a regional locale.

```bash
readio voices list --model piper --lang en-us
readio voices list --engine piper --model en_US-amy-medium
```

Managed Pocket prompts are reference assets rather than target-bound catalog voices. Inspect them with metadata-only filters; listing does not open a Pocket model or download prompt WAV files:

```bash
readio voices prompts --engine pocket --dataset alba
readio voices prompts --engine pocket --variant casual --license cc-by-4.0 --offline
readio synth PROJECT --engine pocket --model BUNDLE_ID --voice-prompt kyutai-tts-voices:alba-mackenna/casual
```

`--voice`, `--voice-file`, and `--voice-prompt` are mutually exclusive. Offline prompt listing needs cached catalog metadata; offline synthesis also needs the managed WAV cached.

Supertonic selects catalog voices such as `F1` on model `supertonic-3`. Readio maps `en-us` to the engine language `en` and rejects unsupported base languages:

```bash
readio voices list --engine supertonic --lang en-us
readio render --engine supertonic --model supertonic-3 --voice F1 --lang en-us "Hello"
```

InflectSynth is optional (`python -m pip install "readio[inflect]"`), English-only, and uses public metadata discovery for its current model targets such as `nano-v2` and `micro-v2`. Each target exposes the fixed `default` voice; `inflectsynth` is accepted as an alias for `inflect`.

```bash
readio voices list --engine inflect --lang en-us
readio voices show inflect:nano-v2/default
readio render --engine inflect --model nano-v2 --voice default --lang en-us --speed 1.1 --voice-level calibrated "Hello from Inflect."
```

The CLI exposes shared speed and voice-level calibration; variation and seed are API engine options. Speaker selection, reference voices, pronunciation overrides, and native word timings are unsupported. InflectSynth 0.1.1 does not advertise a trustworthy capacity maximum, so Readio leaves capacity unknown instead of guessing a token limit. Readio owns request boundaries and capacity policy; InflectSynth owns G2P and runtime/model internals, while ONNXVoice is a transitive dependency that Readio does not import or manage.

Project initialization converts supported filesystem document inputs through ssmdconvert. Ordinary document inputs include text/Markdown, HTML, PDF, DOCX, EPUB, and SSMD. The original file is retained under `source/` as provenance; the editable semantic input is canonical SSMD under `document/document.ssmd.md`. Planning reads the persisted SSMD, not the original source. Editing it replans changed semantics. If the source snapshot changes, status reports the provenance change but does not silently reconvert; initialize a new project from the updated source to ingest it. `readio project init novel.epub` creates one combined document project. `.ssmdbook` bundles are multi-chapter audiobook inputs and are rejected by generic project initialization.

## Book-source audiobook ingestion

```text
readio audiobook chapters SOURCE [--json]
readio audiobook init SOURCE [--chapters SPEC] [-o PROJECT] [--json]
```

`SOURCE` may be an EPUB file, an existing `.ssmdbook` directory, or an `.ssmdbook.zip` file created by ssmdconvert. `chapters` reports metadata and selectable source chapter numbers. `init` defaults to all chapters and otherwise accepts comma-separated numbers and inclusive ranges such as `2-4,7`. Selectors address available 1-based source numbers, including non-contiguous numbers in bundle subsets, and preserve source order. Selection is Readio state under `.readio/`; it does not filter or copy canonical chapters. EPUB and ZIP inputs are materialized as a full editable `.ssmdbook` directory before Readio attaches state. `--output` names that `.ssmdbook` destination for materialization; an existing directory is attached in place and rejects `--output`. `--language` is a conversion override for EPUB only.

Inspection and conversion use ssmdconvert's public APIs. Readio reads the selected canonical chapter files directly from the workspace and stores only indexes, plans, caches, composition, and outputs under `.readio/`. Edit chapter SSMD in the `.ssmdbook` directory to change semantics; Readio immediately sees the current bytes and invalidates only affected content. A dirty workspace is reported without blocking planning or silently changing `manifest.json`. After editing, run `ssmdconvert book refresh WORKSPACE` to refresh canonical chapter hashes. Deleting `.readio/` removes only Readio-derived state; rerun `readio audiobook init WORKSPACE` to recreate it.

Export the composed audiobook master with embedded chapter metadata using the audiobook-only command:

```bash
readio audiobook export PROJECT --format m4b --output book.m4b \
  --title "Optional title" --author "Optional author" --cover cover.jpg --bitrate 96k
```

Title and author default from the book metadata. `--cover` is optional and accepts an explicit JPEG/PNG file; automatic source cover extraction is not part of the current ssmdconvert integration, so provide a cover explicitly. M4B uses AAC with a Readio default bitrate of 192k. Existing unrelated output files require `--force`; unchanged Readio-owned outputs are safely reusable/replaced. A profile can supply these options with `--profile AUDIOBOOK_TOML`; install the optional `readio[audioexport]` extra first. Readio still validates the composition timeline and audiobook metadata before profile encoding. Generic `readio export` supports FLAC and Opus; `.ogg` continues to mean Ogg/Vorbis.

## Persistent project status

`readio status` discovers the project by walking upward from the current directory. Human output shows the project root, source format, each stage state/reason, dependency blocking, and the primary next command. Structured output preserves stable reason codes and includes `issues` plus `next_actions`.

Plan artifact reasons include `plan.index.missing`, `plan.index.invalid`, `plan.artifact.missing`, `plan.artifact.hash_mismatch`, `plan.artifact.plan_id_mismatch`, and `plan.stale.document_format_mismatch`. A wrong-format legacy plan is therefore rebuilt with `readio plan` instead of being silently reused.

## Synthesis progress and JSON

Project `synth` and `preview` accept the shared `--progress` / `--no-progress` option. Interactive progress is written to stderr and includes the resolved profile, cache counts, model-loading phase, and segment IDs with complete renderer text; audiobook synthesis also prints scope-transition headings. `-v` and `-vv` select the existing INFO and DEBUG logging levels; use them for bounded stage, runtime, timing, and cache diagnostics.

`readio synth --json` emits one final JSON object on stdout. It includes the project, scope, plan ID, profile identity and engine/model/voice/language, selector/count, and cache reuse/render counts. Progress and logs remain on stderr, and automatic progress is disabled for JSON unless explicitly forced.

## Mastering profiles

Composition, preview, and bounded `render` default to `--mastering spoken-word` (`-16 LUFS`, `-1 dBTP`). Select `spoken-word-dual-mono` (`-19/-1`), `broadcast-ebu` (`-23/-1`), `peak-safe` (no LUFS target, `-1 dBTP`), or `off` (no target or ceiling). Readio does not offer an ACX LUFS preset: ACX compliance requires separate RMS, peak, and noise-floor checks.

`--target-lufs` and `--true-peak-ceiling-dbtp` are expert numeric overrides; omitted values inherit from the selected profile. Choose `peak-safe` or `off` to disable inherited processing. `--peak-policy reduce_gain` preserves transparent constant-gain behavior and reduces requested gain when needed to meet the true-peak ceiling; it is not a limiter. Use `--peak-policy error` to fail instead of reducing gain.

Human output and `--json` include the selected profile, before/after integrated loudness, sample and true peaks, requested/applied gain, target status, and warnings. Progress separates assembly and loudness finalization (analysis, gain, and cached post-gain metrics) from AudioJob, WAV, timeline, hashing, and state writes. Runtime timings are diagnostic only and never enter composition identity.

## Composition progress

`readio compose PROJECT` shares the progress policy with synthesis, preview, render, and project rendering. Progress is enabled automatically on an interactive terminal, can be forced with `--progress`, and can be disabled with `--no-progress`. All progress is written to stderr.

Composition events identify the current speech segment and operation, show completed and total segments, and show an approximate ETA only after enough segment processing has completed. The ETA covers segment processing and does not predict assembly, complete-output loudness or true-peak processing, or artifact writing. Those stages are rendered separately so all segments reaching 100 percent does not imply that the master is complete.

With `--json`, stdout remains one JSON document. Explicit progress remains on stderr, and progress callbacks are runtime observations only. They do not enter composition IDs, AudioJob serialization, timelines, or composition state identities.

`readio plan` is a project command family: `build` creates semantic Utterplan artifacts (safe repair by default), `inspect` reviews active plans and persisted attempts, `repair` retries an attempt without editing source, `roles` inspects SSMD roles, and `bind` / `unbind` manage project-local acoustic settings. It never selects an engine or loads TTS. Use `readio render --dry-run` to inspect the complete execution plan for one-shot input.

## Planning progress

`readio plan build [PROJECT]` (and bare `readio plan`) shares the existing `--progress` / `--no-progress` option. Progress is automatic on interactive terminals, disabled by default for non-TTY and JSON output, and can be forced explicitly. It is written only to stderr; `readio plan build PROJECT --json` keeps stdout as one parseable JSON document, including when used with `--progress`.

Planning progress identifies the current scope and operational phases such as document parsing, local spaCy model loading, linguistic analysis passes, spoken-text preparation, segmentation, and finalization. Percentages and ETA describe completed project scopes only; Readio does not invent progress inside one opaque linguistic run. Utterplan still performs all semantic SSMD interpretation. The selected linguistic behavior is controlled by the existing `reader.spacy` policy (`auto`, an explicit local model tier, or `off`); progress adds observability without changing that policy or plan identity.

```bash
readio plan build . --progress
readio plan build . --no-progress
readio plan build . --json --progress
```

## Planning attempts, inspection, and repair

Planning writes each run to a durable per-scope attempt before activation. A blocked or interrupted attempt remains available for review, but its candidates never replace active plan artifacts and are never eligible for synthesis. Activation is transactional: only a complete, renderable attempt updates the active plan index. Safe renderability repair is the default; use `--renderability strict` when auditing the unmodified planner output.

```bash
# Strict audit; any unrenderable segments are persisted for inspection.
readio plan build . --renderability strict

# Inspect the latest attempt, its issues, and suggested safe repairs.
readio plan inspect . --attempt latest --issues --repairs --json

# Inspect the active plan instead of the latest attempt.
readio plan inspect . --attempt active

# Preview, then retry a blocked/incomplete attempt without editing source files.
readio plan repair . --attempt ATTEMPT_ID --dry-run
readio plan repair . --attempt ATTEMPT_ID
```

`inspect` accepts `--scope`, `--unit`, `--segment`, and `--source-context` for focused semantic and source review. `--attempt` may be `latest`, `active`, or a concrete attempt ID. `repair` defaults to the latest attempt, supports dry-run, and compiles current project documents; it does not rewrite SSMD or other source files. Matching renderable scopes may be reused across retries. A failed repair leaves the prior active plan untouched.

Attempts and candidates are stored under `plan/attempts/` within the project state root (`.readio/` for attached audiobooks). `readio status --json` reports a separate `planning_attempt` object and includes blocked/incomplete attempts in `issues` and the next action; this does not change the active plan's stage state. The public Python API exposes the same operations as `app.projects.inspect_plan(...)` and `app.projects.repair_plan(...)`.
