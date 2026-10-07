# Readio projects

A Readio project is a persistent audio build graph recognized by `project.json`. Standalone document projects keep their state at the project root (conventionally `*.readio`). An attached audiobook's public project root is the canonical `.ssmdbook` workspace, while its project manifest and all disposable Readio state live in `.ssmdbook/.readio/`.

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

`readio plan roles` discovers logical roles from project source before a semantic plan exists. It reports each role's effective engine-qualified target, voice, and origin. Bind or remove a project override with semantic voice references:

```bash
readio plan bind host kokoro:v1.0/af_sarah
readio plan bind guest piper:en_US-amy-medium
readio plan roles
readio plan unbind host
```

New bindings are stored role-centrically in `project.json` under `settings.ssmd.role_bindings`. Each entry contains structured `engine` and canonical `voice`, plus optional `target_id`; selector provenance is not persisted. Binding a semantic reference preserves its target identity and does not set a project-wide provider. For native voice IDs, pass `--engine` if Readio cannot infer the engine and use a semantic reference when the target must be explicit. Binding and unbinding leave SSMD source, user-global roles, and Utterplan `plan_id` unchanged.

A document-local SSMD binding remains authoritative; a project binding is rejected rather than saved as an ineffective override. SSMD `voice_bindings` syntax remains provider-qualified and unchanged. If the same role is bound in multiple document provider namespaces, or conflicting unscoped legacy project bindings exist for that role, Readio reports an ambiguity instead of choosing one.

v0.3 manifests using `settings.ssmd.voice_bindings` require explicit `readio project migrate PROJECT`; normal project reads reject the old structure instead of applying provider fallbacks. Migration preserves a backup and stops on ambiguous/conflicting role targets. SSMD document `voice_bindings` remains namespaced according to the external SSMD format.

Voice resolution follows `document > invocation --voice-bind > project > global config role > direct semantic reference or context-resolved native voice ID`. A project role target is an acoustic synthesis setting. Changing it leaves the semantic plan current, marks active synthesis stale with `synthesis.stale.project_voice_bindings_changed`, blocks composition and output, and makes `readio synth` the next action. The content-addressed synthesis cache is retained.

Each bound segment is routed to its target's engine. Unbound segments use the normal project synthesis selection. One project can use Kokoro, Piper, Pocket, or Kitten on different roles without choosing one project-wide engine. `readio plan roles --engine ENGINE` filters inspection output and does not override bindings.

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

Settings are stored in the Readio project manifest: at the root for standalone schema-v3 projects and under `.readio/project.json` for attached schema-v4 audiobooks. Updates are atomic, retain `settings.ssmd` and unknown namespaces, and never rewrite the canonical `.ssmdbook/manifest.json`. The CLI exposes named fields only. Relative output and asset paths are interpreted relative to the public project root. `force` and `refresh` are invocation-only and are never saved.

The Python API provides immutable `ProjectSettings`, `ProjectSynthesisSettings`, and `ProjectSettingsPatch`. `app.projects.configure(project, settings)` replaces the supported settings sections; `update_settings(project, patch)` changes only sections present in the patch. In a patch, `UNSET` leaves a section unchanged, `None` clears it, and a concrete value replaces it. `settings(project)` returns detached values.

Persisted synthesis choices are resolved before global and engine defaults. Explicit run requests override saved choices without changing the manifest. Requestless `synthesize`, `compose`, generic `export`, and incremental `build` use saved settings. Composition-only changes stale composition and output; generic or audiobook export-only changes stale only output. Synthesis caches and previous outputs are retained for reuse.

For audiobook projects, `app.audiobooks.export(project)` uses saved M4B path, metadata, cover, and bitrate defaults. `app.audiobooks.build(project)` plans, synthesizes, composes, then exports M4B using the saved settings. `create_project(..., settings=...)` can persist those choices during project creation. `readio status` reports when a prior M4B no longer matches the desired audiobook settings.
Use `readio render --file episode.ssmd --dry-run --json` for one-shot execution planning. `readio plan` is reserved for persistent project build and role management.

## Book-source audiobook projects

Create a chapter-scoped project from an EPUB or ssmdconvert `.ssmdbook` directory/ZIP bundle. EPUB and ZIP sources are materialized as an editable `.ssmdbook` directory; an existing directory is attached in place. Then use the ordinary Readio stages from the book workspace:

```bash
readio audiobook chapters novel.epub
readio audiobook init novel.epub --chapters 2-20
readio audiobook chapters novel.ssmdbook
readio audiobook init novel.ssmdbook.zip --chapters 3-4,7
cd novel.ssmdbook
readio plan
readio synth --voice en-ko-01
readio compose
readio export --format m4a
readio audiobook export . --format m4b --cover cover.jpg
# Or run the complete incremental pipeline:
readio render novel.ssmdbook --format m4a
```

Chapter selectors address available 1-based source chapter numbers, including non-contiguous numbers in bundles. Selection and chapter metadata are persisted in Readio state under `.readio/document/index.json`; the canonical book manifest and chapter list remain complete and unfiltered.

ssmdconvert owns the editable chapter files under `chapters/` within the canonical `.ssmdbook` workspace (or the paths listed in its manifest). Readio reads those canonical bytes directly and never copies chapters into `.readio/`. Plans, indexes, synthesis caches, composition parts, the master, and outputs are disposable Readio state under `.readio/`. `readio plan` builds one plan per selected scope; synthesis and composition operate across selected scopes in book order and preserve chapter boundaries in `.readio/composition/timeline.json`.

Manual chapter edits are immediately visible to planning and invalidate only affected chapter plans and speech. A manifest/chapter digest mismatch is reported as dirty workspace provenance, but Readio continues using current bytes and never rewrites canonical metadata. Run `ssmdconvert book refresh novel.ssmdbook` after editing to update manifest hashes and restore workspace cleanliness. The `.ssmdbook` remains usable by ssmdconvert and other tools without Readio. Deleting `.readio/` removes only local indexes, plans, caches, composition, and output; rerun `readio audiobook init novel.ssmdbook` to recreate derived state. Generic EPUB input is still converted as one combined document.

Audiobook projects support an audiobook-specific M4B export with embedded chapter metadata: `readio audiobook export PROJECT --format m4b`. Title and author default from book metadata; callers may override them. M4B uses AAC with a Readio default of 192k. Cover art is optional and explicit-only (`--cover image.jpg` or PNG); automatic source cover extraction is not part of the current ssmdconvert integration, so provide a JPEG/PNG explicitly. The selected cover is hashed into export identity.

M4B is deliberately excluded from generic `readio export`. Generic exports include FLAC and Opus; `.ogg` remains Ogg/Vorbis, while `.opus` is distinct. Changing the master, chapter timeline/title, resolved book metadata, cover, or bitrate invalidates only the M4B output, not planning or synthesis. Untracked or user-modified destination files require `--force`; unchanged Readio-owned outputs can be reused or replaced atomically.

For an attached schema-v4 audiobook, the canonical `.ssmdbook/manifest.json` and chapter files remain editable source. All Readio indexes, Utterplan artifacts, synthesis cache/trace, AudioCompose files, master, and generated outputs live under `.ssmdbook/.readio/`. Portable `.ssmdbook.zip` archives exclude this disposable state; deleting it does not delete or rewrite the book.
Readio-owned manifest and audio writes use temporary siblings followed by atomic replace; Readio does not update the canonical ssmdbook manifest.
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

The plan index can contain multiple independent scopes such as chapters. Each scope points to a canonical `.utterplan.toml` artifact. Indexed legacy `.utterplan.json` plans are stale and must be regenerated with `readio plan`; Readio does not load or migrate them during normal project loading, and plan files are not custom collection manifests.

## Planning attempts and safe repair

`readio plan` defaults to safe renderability repair; `readio plan build . --renderability strict` retains the unmodified audit path. Every invocation persists its planning attempt and per-scope candidates under `plan/attempts/` in the project state root. Blocked and incomplete candidates are inspection-only: they cannot replace the active plan index or be consumed by synthesis. Promotion occurs only after all selected scopes produce renderable plans.

Use `readio plan inspect . --attempt latest --issues --repairs` to review diagnostics and safe repair assessments. Select `--attempt active` to inspect the current synthesis-eligible plan, or provide an attempt ID. Focus review with `--scope`, `--unit`, `--segment`, and `--source-context`. `readio plan repair . --attempt ID --dry-run` previews a retry; omit `--dry-run` to compile current project documents with safe repair and activate only if the full attempt succeeds. Repair may reuse unchanged renderable scopes and never edits source files. A failed or interrupted retry leaves existing active artifacts untouched.

`readio status --json` reports the latest `planning_attempt` separately from active PLAN stage state. Blocked/incomplete attempts are surfaced as issues with an inspection action, while synthesis continues to use only the active index. The public project API provides `inspect_plan()` and `repair_plan()` with typed inspection, issue, repair, and result values.

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

In schema-3 projects, `project.json` records original source metadata and `document/index.json` lists editable semantic scopes as SSMD (`input_format: ssmd`). Their canonical content is persisted under `document/*.ssmd.md`; planning, role discovery, and status use these files rather than reopening the original source. Editing semantic SSMD invalidates the affected plan. Changed source provenance is reported but does not silently reconvert persisted semantics. v0.3 schema-2 projects must be upgraded explicitly with `readio project migrate PROJECT`.

Attached audiobook schema-v4 projects keep the public project root at the `.ssmdbook` workspace and store Readio's `project.json`, selection/index, plans, caches, composition, and outputs in `.readio/`. `DocumentScope.path` points to canonical workspace chapter files; Readio refreshes only its local index from current bytes. A dirty workspace is diagnostic, not a reason to copy, restore, or rewrite chapters. Readio's own operations remain usable until the user explicitly refreshes hashes with ssmdconvert.

## Synthesis observability

`readio synth` resolves and reports the project, source, plan, profile, engine/model/voice, and cache reuse before loading an engine. Its progress counts stale renderer segments globally, includes stable unit/segment IDs and complete renderer text, and shows chapter/scope headings when the active scope changes. Public progress events retain exact text and chapter metadata. `readio preview` uses the same progress events. Progress and logs use stderr; `--json` keeps stdout as one final JSON object containing `scope`, `plan_id`, `profile`, `selection`, and `cache`.

Use `--no-progress` for quiet automation or `--progress` to force progress. Repeat the global verbosity flag (`-v` or `-vv`) for INFO or bounded engine/runtime diagnostics. Diagnostics identify decisions, paths, counts, timings, and cache keys; raw tensors, waveforms, and embeddings are never dumped.

## Composition observability

`readio compose PROJECT` reports progress from the audiocompose layer that performs source loading, operations, resampling, assembly, and complete-output loudness. Readio maps generic item metadata to speech segment IDs and renders the active operation on stderr.

Interactive terminals update a current line. Forced non-TTY progress is throttled. `--progress` enables output, `--no-progress` suppresses it, and automatic progress is disabled by `--json` unless explicitly forced. JSON stdout remains uncontaminated.

The composition ETA is approximate and covers only segment processing. Progress separately reports master assembly, loudness analysis/gain/post-gain metrics, AudioJob and master WAV writes, timeline/state writes, and artifact hashing; each finalization duration is diagnostic-only. Preview and incremental `render PROJECT` use the same composition callback path when they rebuild composition. Runtime timings do not affect composition IDs, timelines, AudioJob files, or composition state identity.
A complete cache hit emits no engine-open phase and does not load the model. Active `synthesis/profile.json` and `synthesis/trace.json` are the persisted status truth; changing content or profile invalidates only affected synthesis keys.
