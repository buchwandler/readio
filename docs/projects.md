# Readio projects

This page covers Readio's persistent generic document-project workflow, where `project.json` stores Readio-owned plans, synthesis state, composition, and output provenance. Native audiobooks use SSMDConvert `.ssmdbook` workspaces and specialist-owned artifacts instead; they are not Readio projects, have no Readio `project.json`, and never create `.readio/`.

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
readio project settings clear . --section export
```

Generic project settings are stored in the Readio project manifest at the root of standalone projects. Updates are atomic and retain `settings.ssmd` and unknown namespaces. Older attached-book project records may still exist for compatibility, but native `.ssmdbook` workflows do not create, attach, or consume them. Relative output and asset paths are interpreted from the generic project root; `force` and `refresh` are invocation-only.

The Python API provides immutable `ProjectSettings`, `ProjectSynthesisSettings`, and `ProjectSettingsPatch`. `app.projects.configure(project, settings)` replaces the supported settings sections; `update_settings(project, patch)` changes only sections present in the patch. In a patch, `UNSET` leaves a section unchanged, `None` clears it, and a concrete value replaces it. `settings(project)` returns detached values.

Persisted synthesis choices are resolved before global and engine defaults. Explicit run requests override saved choices without changing the manifest. Requestless `synthesize`, `compose`, generic `export`, and incremental `build` use saved settings. Composition-only changes stale composition and output; export-only changes stale only output. Synthesis caches and previous outputs are retained for reuse.

Native audiobook requests do not use `ProjectSettings` or `ProjectRef`. Select the VoiceRender and AudioExport profiles when invoking `readio audiobook build` or the relevant individual stage; profile IDs and stage outputs remain owned by specialist packages.

### AudioExport profile exports

AudioExport is installed as a core dependency. Profiles are selected per operation: generic file exports use `--profile`, while native audiobook export uses `readio audiobook export WORKSPACE --variant ID --profile AUDIOEXPORT_PROFILE`. Generic multi-output profiles can be used with `readio export --all --out-dir DIR`.

```bash
readio export . --profile export.toml --format mp3
readio export . --profile export.toml --all --out-dir output/
readio audiobook export novel.ssmdbook --variant NARRATION_VARIANT \
  --profile audiobook.toml -o novel.m4b
readio project settings set . --export-profile export.toml
```

Generic saved profile paths are relative to the project root. Direct command/API options take precedence for that invocation without rewriting settings. `readio status` compares generic-project profile identity and output path; profile changes stale only the relevant output. Native audiobook stage profiles and outputs are owned by AudioExport and the other specialist packages, not the Readio project status graph. `readio speak` remains on its streaming sink.
Use `readio render --file episode.ssmd --dry-run --json` for one-shot execution planning. `readio plan` is reserved for persistent project build and role management.

## Native `.ssmdbook` audiobook workspaces

An audiobook workspace is the editable SSMDConvert `.ssmdbook` directory. Readio provides orchestration and presentation; the specialist packages own the artifacts and currentness rules. No Readio `project.json` or `.readio/` state is created. Existing `.readio/` content is ignored with a warning and left unchanged.

Convert a source or reopen an existing workspace, then build by chapter selector:

```bash
readio audiobook chapters novel.epub
readio audiobook convert novel.epub --chapters 5-17 -o novel.ssmdbook
readio audiobook profile create narration.renderprofile.toml \
  --engine piper --target-id en_US-amy-medium --language en-US
readio audiobook build novel.ssmdbook --profile narration.renderprofile.toml \
  --chapters 5-17 --stop-after render-plan --json
readio audiobook status novel.ssmdbook --json
```

A build result exposes the exact profile-fingerprinted variant. Resume it with the same `--variant` for `synth`, `compose`, and `export`; specialist CLIs can use that exact name too. A `.ssmdbook` created manually or by Readio uses the same artifact paths, so stages can continue in either direction without initialization or migration.

```bash
# Specialist commands equivalent to the Readio stages
ssmdconvert book convert novel.epub -o novel.ssmdbook --chapters 5-17
utterplan compile-book novel.ssmdbook --chapters 5-17
utterplan render-book novel.ssmdbook --profile narration.renderprofile.toml --chapters 5-17
# Set VARIANT to the exact returned RenderPlan directory name.
voicerender render-book novel.ssmdbook --variant "$VARIANT" --chapters 5-17
audiocompose compose-book novel.ssmdbook --variant "$VARIANT" --chapters 5-17
audioexport export-book novel.ssmdbook --variant "$VARIANT" --chapters 5-17 -o novel.m4b
```

`readio audiobook status` reports stage-owned artifacts and workspace dirtiness without refreshing, converting, or compiling. Edit canonical chapter files in the workspace; use `ssmdconvert book refresh WORKSPACE` explicitly when you want to refresh manifest hashes. The native audiobook workflow never changes generic document-project settings, status, or `readio speak`.

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

Native `.ssmdbook` workspaces are not Readio generic projects and are not discovered by `readio status`; use `readio audiobook status WORKSPACE [--variant ID]` to inspect owner artifacts. The workflow ignores existing `.readio/` data without modifying it and leaves refresh decisions to `ssmdconvert book refresh`.

## Synthesis observability

`readio synth` resolves and reports the project, source, plan, profile, engine/model/voice, and cache reuse before loading an engine. Its progress counts stale renderer segments globally, includes stable unit/segment IDs and complete renderer text, and shows chapter/scope headings when the active scope changes. Public progress events retain exact text and chapter metadata. `readio preview` uses the same progress events. Progress and logs use stderr; `--json` keeps stdout as one final JSON object containing `scope`, `plan_id`, `profile`, `selection`, and `cache`.

Use `--no-progress` for quiet automation or `--progress` to force progress. Repeat the global verbosity flag (`-v` or `-vv`) for INFO or bounded engine/runtime diagnostics. Diagnostics identify decisions, paths, counts, timings, and cache keys; raw tensors, waveforms, and embeddings are never dumped.

## Composition observability

`readio compose PROJECT` reports progress from the audiocompose layer that performs source loading, operations, resampling, assembly, and complete-output loudness. Readio maps generic item metadata to speech segment IDs and renders the active operation on stderr.

Interactive terminals update a current line. Forced non-TTY progress is throttled. `--progress` enables output, `--no-progress` suppresses it, and automatic progress is disabled by `--json` unless explicitly forced. JSON stdout remains uncontaminated.

The composition ETA is approximate and covers only segment processing. Progress separately reports master assembly, loudness analysis/gain/post-gain metrics, AudioJob and master WAV writes, timeline/state writes, and artifact hashing; each finalization duration is diagnostic-only. Preview and incremental `render PROJECT` use the same composition callback path when they rebuild composition. Runtime timings do not affect composition IDs, timelines, AudioJob files, or composition state identity.
A complete cache hit emits no engine-open phase and does not load the model. Active `synthesis/profile.json` and `synthesis/trace.json` are the persisted status truth; changing content or profile invalidates only affected synthesis keys.
