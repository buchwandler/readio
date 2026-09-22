# Readio projects

A Readio project is a persistent audio build graph recognized by `project.json`.
The `.readio` suffix is conventional; the manifest is authoritative.

```bash
readio project init manuscript.md -o manuscript.readio
readio plan manuscript.readio
readio synth manuscript.readio --voice de-ko-01
readio compose manuscript.readio
readio export manuscript.readio --format mp3
```

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

Use `--no-progress` for quiet automation or `--progress` to force progress. Repeat the existing global verbosity flag (`-v` or `-vv`) for INFO or bounded backend/runtime diagnostics. Diagnostics identify decisions, paths, counts, timings, and cache keys; raw tensors, waveforms, and embeddings are never dumped.

A complete cache hit emits no engine-open phase and does not load the model. Active `synthesis/profile.json` and `synthesis/trace.json` are the persisted status truth; changing content or profile invalidates only affected synthesis keys.
