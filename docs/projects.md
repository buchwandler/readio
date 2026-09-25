# Readio projects

A Readio project is a persistent audio build graph recognized by `project.json`.
The `.readio` suffix is conventional; the manifest is authoritative.

```bash
readio project init episode.ssmd -o episode.readio
cd episode.readio
readio plan roles
readio plan bind narrator en_us-ko-4
readio plan                         # build semantic plan
readio synth
readio compose --progress
readio export --format mp3
```

## Project-local SSMD role bindings

`readio plan roles` discovers logical SSMD roles from project source before a semantic plan exists and shows each effective voice and its source. Bind or remove a project override with:

```bash
readio plan bind narrator en_us-ko-4
readio plan bind host en_us-ko-7
readio plan unbind narrator
```

Bindings are stored in `project.json` under `settings.ssmd.voice_bindings`, provider-keyed, and canonicalized to concrete voice IDs. They do not edit SSMD source, mutate user-global `readio roles` settings, or change Utterplan `plan_id`. A document-local SSMD binding remains authoritative; when it exists in any relevant scope, project binding is rejected rather than saved as an ineffective override. Role inspection reports per-scope effective values when document bindings differ between scopes.

Voice resolution follows `document > invocation --voice-bind > project > global config role > direct voice`. A project binding is an acoustic synthesis setting. Changing it leaves the semantic plan current, marks active synthesis stale with `synthesis.stale.project_voice_bindings_changed`, blocks composition and output, and makes `readio synth` the next action. The content-addressed synthesis cache is retained.

Use `readio render --file episode.ssmd --dry-run --json` for one-shot execution planning. `readio plan` is reserved for persistent project build and role management.

## EPUB audiobook projects

Create a chapter-scoped project with the EPUB-specific ingestion commands, then use the ordinary Readio stages:

```bash
readio audiobook chapters novel.epub
readio audiobook init novel.epub --chapters 2-20
cd novel.readio
readio plan
readio synth --voice en-ko-01
readio compose
readio export --format m4a
# Or run the complete incremental pipeline:
readio render novel.readio --format m4a
```

Chapter numbers are 1-based and match the flat order printed by `readio audiobook chapters`, including nested navigation entries. The selector accepts `all`, single numbers, inclusive ranges, and comma-separated combinations. Selection order follows the EPUB chapter order, and the selected source numbers and scope IDs are persisted in `document/index.json`.

Each selected chapter is extracted through the public `epub2text` chapter-document API and stored as Markdown under `document/chapters/`. This Markdown is the editable semantic source. `readio plan` builds one plan per indexed scope, while synthesis and composition operate across all scopes in order. Identical speech can share the content-addressed synthesis cache. Composition preserves scope-qualified item IDs, per-chapter part directories, and chapter start samples in `composition/timeline.json`.

The copied EPUB under `source/` records extraction provenance. Changing chapter Markdown does not trigger EPUB extraction and only invalidates affected planning and speech work. Changing the copied EPUB produces `source.stale.hash_changed`; reinitialize the project to use the changed source. Readio will not silently remap chapter numbers or refresh extracted documents. The EPUB is an ingestion format, not a direct `InputDocument` or an audiobook-specific build pipeline.

M4B encoding and embedded container chapter metadata are non-goals of this first version. The project retains chapter boundaries in its timeline so a future generic export capability can use them.

Projects preserve a source snapshot and normalized document, an engine-neutral
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

Project document metadata keeps editable `input_format` separate from semantic `document_format`. SSMD remains SSMD through project planning; legacy metadata without `document_format` infers SSMD only when its input format is SSMD.

## Synthesis observability

`readio synth` resolves and reports the project, source, plan, profile, engine/model/voice, cache reuse, and unit progress before loading an engine. `readio preview` uses the same progress events. Progress and logs use stderr; `--json` keeps stdout as one final JSON object containing `scope`, `plan_id`, `profile`, `selection`, and `cache`.

Use `--no-progress` for quiet automation or `--progress` to force progress. Repeat the global verbosity flag (`-v` or `-vv`) for INFO or bounded engine/runtime diagnostics. Diagnostics identify decisions, paths, counts, timings, and cache keys; raw tensors, waveforms, and embeddings are never dumped.

## Composition observability

`readio compose PROJECT` reports progress from the audiocompose layer that performs source loading, operations, resampling, assembly, and complete-output loudness. Readio maps generic item metadata to speech segment IDs and renders the active operation on stderr.

Interactive terminals update a current line. Forced non-TTY progress is throttled. `--progress` enables output, `--no-progress` suppresses it, and automatic progress is disabled by `--json` unless explicitly forced. JSON stdout remains uncontaminated.

The composition ETA is approximate and covers only segment processing. After segment processing, Readio reports `Assembling master`, `Finalizing loudness and true peak`, and `Writing composition artifacts` as separate phases. Preview and incremental `render PROJECT` use the same composition callback path when they rebuild composition. Progress is runtime-only and does not affect composition IDs, timelines, AudioJob files, or composition state.
A complete cache hit emits no engine-open phase and does not load the model. Active `synthesis/profile.json` and `synthesis/trace.json` are the persisted status truth; changing content or profile invalidates only affected synthesis keys.
