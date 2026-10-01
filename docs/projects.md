# Readio projects

A Readio project is a persistent audio build graph recognized by `project.json`.
The `.readio` suffix is conventional; the manifest is authoritative.

```bash
readio project init episode.ssmd -o episode.readio
cd episode.readio
readio plan roles
readio plan bind host kokoro:v1.0/af_sarah
readio plan bind guest piper:en_US-amy-medium
readio plan                         # build semantic plan
readio synth
readio compose --progress
readio export --format mp3
```

## Project-local SSMD role targets

`readio plan roles` discovers logical roles from project source before a semantic plan exists. It reports each role's effective voice, engine, provider, and source. Bind or remove a project override with semantic voice references:

```bash
readio plan bind host kokoro:v1.0/af_sarah
readio plan bind guest piper:en_US-amy-medium
readio plan roles
readio plan unbind host
```

New bindings are stored role-centrically in `project.json` under `settings.ssmd.role_bindings`. Each entry contains structured `engine` and canonical `voice`, plus optional `target_id`; selector provenance is not persisted. Binding a semantic reference preserves its target identity and does not set a project-wide provider. For native voice IDs, pass `--engine` if Readio cannot infer the engine and use a semantic reference when the target must be explicit. Binding and unbinding leave SSMD source, user-global roles, and Utterplan `plan_id` unchanged.

A document-local SSMD binding remains authoritative; a project binding is rejected rather than saved as an ineffective override. SSMD `voice_bindings` syntax remains provider-qualified and unchanged. If the same role is bound in multiple document provider namespaces, or conflicting unscoped legacy project bindings exist for that role, Readio reports an ambiguity instead of choosing one.

Existing manifests using `settings.ssmd.voice_bindings.<provider>.<role>` remain readable. The optional legacy `settings.ssmd.voice_provider` scopes those provider-keyed inputs when present; it does not choose an engine or affect new `role_bindings`. When no legacy provider is selected, conflicting legacy definitions for the same role are ambiguous. `readio plan bind` writes the new role-centric format without rewriting unrelated legacy settings. There is no automatic migration command.

Voice resolution follows `document > invocation --voice-bind > project > global config role > direct semantic reference or context-resolved native voice ID`. A project role target is an acoustic synthesis setting. Changing it leaves the semantic plan current, marks active synthesis stale with `synthesis.stale.project_voice_bindings_changed`, blocks composition and output, and makes `readio synth` the next action. The content-addressed synthesis cache is retained.

Each bound segment is routed to its target's engine. Unbound segments use the normal project synthesis selection. One project can therefore use Kokoro for one role and Piper for another, without choosing one provider project-wide. `readio plan roles --provider PROVIDER` filters inspection output and does not override bindings.

## Desired pipeline settings

Save supported desired settings before planning, synthesis, composition, or export runs:

```bash
readio project settings show . --json
readio project settings set . --engine piper --voice en_US-amy-medium \
  --mastering spoken-word --target-lufs -18 \
  --export-format mp3 --export-output output/episode.mp3
readio project settings set . --audiobook-output output/book.m4b \
  --audiobook-title "My audiobook" --audiobook-bitrate 96k
readio project settings clear . --section export
```

Settings are stored in `project.json` under the existing `settings` object. Updates are atomic, retain `settings.ssmd` and unknown namespaces, and keep schema version 2. The CLI exposes named fields only. Relative output and asset paths are interpreted relative to the project root. `force` and `refresh` are invocation-only and are never saved.

The Python API provides immutable `ProjectSettings`, `ProjectSynthesisSettings`, and `ProjectSettingsPatch`. `app.projects.configure(project, settings)` replaces the supported settings sections; `update_settings(project, patch)` changes only sections present in the patch. In a patch, `UNSET` leaves a section unchanged, `None` clears it, and a concrete value replaces it. `settings(project)` returns detached values.

Persisted synthesis choices are resolved before global and engine defaults. Explicit run requests override saved choices without changing the manifest. Requestless `synthesize`, `compose`, generic `export`, and incremental `build` use saved settings. Composition-only changes stale composition and output; generic or audiobook export-only changes stale only output. Synthesis caches and previous outputs are retained for reuse.

For audiobook projects, `app.audiobooks.export(project)` uses saved M4B path, metadata, cover, and bitrate defaults. `app.audiobooks.build(project)` plans, synthesizes, composes, then exports M4B using the saved settings. `create_project(..., settings=...)` can persist those choices during project creation. `readio status` reports when a prior M4B no longer matches the desired audiobook settings.
Use `readio render --file episode.ssmd --dry-run --json` for one-shot execution planning. `readio plan` is reserved for persistent project build and role management.

## Book-source audiobook projects

Create a chapter-scoped project from an EPUB or an ssmdconvert `.ssmdbook` directory/ZIP bundle, then use the ordinary Readio stages:

```bash
readio audiobook chapters novel.epub
readio audiobook init novel.epub --chapters 2-20
readio audiobook chapters novel.ssmdbook
readio audiobook init novel.ssmdbook.zip --chapters 3-4,7
cd novel.readio
readio plan
readio synth --voice en-ko-01
readio compose
readio export --format m4a
readio audiobook export . --format m4b --cover cover.jpg
# Or run the complete incremental pipeline:
readio render novel.readio --format m4a
```

Book inspection and conversion are delegated to ssmdconvert's public API. Chapter selectors address available 1-based source chapter numbers, including non-contiguous numbers in bundle subsets. Selected chapters retain source order; their source numbers and scope IDs are persisted in `document/index.json`.

Readio stores the resulting standalone SSMD chapter documents under `document/chapters/` as editable semantic inputs. `readio plan` builds one plan per indexed scope, while synthesis and composition operate across all scopes in order. Identical speech can share the content-addressed synthesis cache. Composition preserves scope-qualified item IDs, per-chapter part directories, and chapter start samples in `composition/timeline.json`.

The original EPUB or bundle snapshot under `source/` records provenance. Editing chapter SSMD replans only affected content and does not trigger source conversion. A changed source snapshot is reported as `source.stale.hash_changed`, but it does not replace or invalidate the persisted semantic SSMD. Reinitialize from the updated source to ingest its changes. Generic EPUB input is converted as one combined document; use the audiobook workflow for chapter-scoped processing.

Audiobook projects support an audiobook-specific M4B export with embedded chapter metadata: `readio audiobook export PROJECT --format m4b`. Title and author default from book metadata; callers may override them. M4B uses AAC with a Readio default of 192k. Cover art is optional and explicit-only (`--cover image.jpg` or PNG); automatic source cover extraction is not part of the current ssmdconvert integration, so provide a JPEG/PNG explicitly. The selected cover is hashed into export identity.

M4B is deliberately excluded from generic `readio export`. Generic exports include FLAC and Opus; `.ogg` remains Ogg/Vorbis, while `.opus` is distinct. Changing the master, chapter timeline/title, resolved book metadata, cover, or bitrate invalidates only the M4B output, not planning or synthesis. Untracked or user-modified destination files require `--force`; unchanged Readio-owned outputs can be reused or replaced atomically.

Projects preserve the original source snapshot and canonical editable SSMD semantics,
`plan/document.utterplan.json`, `plan/index.json`, synthesis cache/trace,
bundle-local Audiocompose files, a composed master/timeline, and encoded output.
All manifest and audio writes use temporary siblings followed by atomic replace.
Mutating project stages use a project lock; `status` is read-only.

## Identity boundaries

- `UtterancePlan.plan_id` identifies semantic speaking content.
- A serialized plan SHA identifies exact persisted bytes.
- `synthesis_profile_id` and `synthesis_key` identify reusable acoustic audio.
- `composition_id` identifies ordered audio plus composition/loudness policy.
- `export_id` identifies a master plus encoder format/options.

Voice, model, and engine changes begin at synthesis. Loudness changes begin at
composition. Codec and bitrate changes begin at export; none require semantic
replanning.

Readio defaults to the `spoken-word` mastering profile (`-16 LUFS/-1 dBTP`). Composition identity includes the requested profile and resolved numeric target/ceiling; analysis timings are excluded. Composition state and typed API/JSON results retain before/after loudness and peak readings, requested/applied gain, target status, and any ceiling-limited warning.

## Commands

`readio plan` does not discover a voice or load a TTS model. `readio preview`
uses a selected range and an alternate profile without changing the active
profile unless `--activate` is supplied. `readio status --json` exposes stage
state and diagnostic reasons. `readio render PROJECT --format mp3` is the
incremental high-level build command.

The plan index can contain multiple independent scopes such as chapters. Each
scope points to a real Utterplan artifact; an `*.utterplan.json` file is never

used as a custom collection manifest.

## Status cockpit

Run `readio status` from the project root or any nested directory. It validates the source, normalized document, every indexed plan artifact, the active synthesis profile/trace, composition, and output. Stale upstream stages block downstream stages; the terminal view prints the first command to run, while `--json` also exposes `issues` and `next_actions`.

Typical recovery is:

```text
PLAN         stale   plan.stale.document_format_mismatch
SYNTHESIS    stale   synthesis.stale.plan_changed blocked by plan
COMPOSITION  stale   composition.stale.synthesis_changed blocked by synthesis
OUTPUT       stale   output.stale.composition_changed blocked by composition

Next:
  readio plan
```

For schema-v2 projects, `project.json` records the original source format, while `document/index.json` records the editable semantic scopes as SSMD (`input_format: ssmd`). Their canonical content is persisted under `document/*.ssmd.md`; planning, role discovery, and status use those files rather than reopening the original source. Editing semantic SSMD invalidates the affected plan. A changed provenance source is reported but does not silently reconvert or invalidate persisted semantics. Legacy schema-v1 metadata without `document_format` infers SSMD only when its input format is SSMD.

## Synthesis observability

`readio synth` resolves and reports the project, source, plan, profile, engine/model/voice, cache reuse, and unit progress before loading an engine. `readio preview` uses the same progress events. Progress and logs use stderr; `--json` keeps stdout as one final JSON object containing `scope`, `plan_id`, `profile`, `selection`, and `cache`.

Use `--no-progress` for quiet automation or `--progress` to force progress. Repeat the global verbosity flag (`-v` or `-vv`) for INFO or bounded engine/runtime diagnostics. Diagnostics identify decisions, paths, counts, timings, and cache keys; raw tensors, waveforms, and embeddings are never dumped.

## Composition observability

`readio compose PROJECT` reports progress from the audiocompose layer that performs source loading, operations, resampling, assembly, and complete-output loudness. Readio maps generic item metadata to speech segment IDs and renders the active operation on stderr.

Interactive terminals update a current line. Forced non-TTY progress is throttled. `--progress` enables output, `--no-progress` suppresses it, and automatic progress is disabled by `--json` unless explicitly forced. JSON stdout remains uncontaminated.

The composition ETA is approximate and covers only segment processing. Progress separately reports master assembly, loudness analysis/gain/post-gain metrics, AudioJob and master WAV writes, timeline/state writes, and artifact hashing; each finalization duration is diagnostic-only. Preview and incremental `render PROJECT` use the same composition callback path when they rebuild composition. Runtime timings do not affect composition IDs, timelines, AudioJob files, or composition state identity.
A complete cache hit emits no engine-open phase and does not load the model. Active `synthesis/profile.json` and `synthesis/trace.json` are the persisted status truth; changing content or profile invalidates only affected synthesis keys.
