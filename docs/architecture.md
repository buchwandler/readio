# Readio architecture

Persistent projects separate semantic planning, canonical speech, editorial composition, and export:

```text
source/document
      |
      | semantic policy and linguistic enrichment
      v
UtterancePlan schema v4
      |\
      | \
      |  +--> resolved pauses, segment order, presentation directives
      |                 |
      |                 v
      |          Readio composition
      |
      +--> canonical speech identity
                    |
                    v
       Readio request lowering
                    |
                    v
       engine session pool
                    |
                    v
       content-addressed speech cache
                    |
                    v
      canonical speech WAV + sidecar
                    |
                    +--> composition timeline and master WAV
                                      |
                                      v
                              codec-specific export
```

## Canonical audiobook workspace and Readio state

An attached audiobook uses a dual-root layout:

```text
Novel.ssmdbook/                  # canonical, editable book
  manifest.json
  chapters/*.ssmd.md
  .readio/                       # disposable Readio state
    project.json
    document/index.json          # chapter selection and local index
    plan/                        # active plan and durable planning attempts
      index.json                 # active, synthesis-eligible scopes
      attempts/                  # isolated candidates and diagnostics
    synthesis/                   # cache and trace
    composition/                 # timeline and master
    output/
```

`Project.root` and public project references identify the `.ssmdbook` workspace; `Project.state_root` is `.readio/`. Chapter scope paths resolve against the workspace, so Readio plans from current chapter bytes without copying them. The public ssmdconvert workspace API reports manifest/chapter dirtiness; Readio may refresh its local index but never rewrites canonical chapter files or manifest hashes. Run `ssmdconvert book refresh` explicitly after editing when you want a clean manifest. Portable `.ssmdbook.zip` archives omit `.readio/`, and deleting that directory removes only Readio-derived state. Standalone schema-v3 document projects remain self-contained and retain their existing layout.

## Semantic plan and speech cache

UtterPlan remains the owner of semantic identity. Its unit hashes may include
resolved pauses and directives because those facts describe the semantic plan.
Readio does not use a unit hash as its acoustic cache atom. Unit selectors are
expanded to ordered, unique `PlanSegment` IDs before synthesis.

Planning is transactional across scopes. Each build writes a durable attempt under `plan/attempts/`; completed candidates and renderability diagnostics remain inspectable even when another scope blocks the build. Only a fully renderable attempt atomically replaces the active plan index and canonical artifacts. Synthesis reads only that active index, never an attempt candidate. Repair retries compile current project documents, may reuse fingerprint-matching renderable scopes, and never rewrite source files. Safe repair is the default; strict mode remains available for auditing.

Each canonical artifact is identified by `readio.canonical-speech.v1` segment
input facts and a `readio.synthesis-segment.v2` key. The speech fingerprint
includes segment text and language, synthesis directives, pronunciation data,
linguistic token facts, the resolved voice/model, G2P and generation settings,
cleanup policy, and calibration identity. It excludes plan IDs, unit grouping,
pauses, prosody, emphasis, fades, final loudness, and codec settings.

The cache is authoritative through its content-addressed WAV and sidecar:

```text
synthesis/
  profile.json
  cache/
    <synthesis-key>.wav
    <synthesis-key>.json
  segments/
    seg-000000.wav       # optional active segment view
  trace.json             # provenance and progress only
```

A sidecar records the speech hash, synthesis key, profile ID, audio digest,
sample rate, channel count, and frame count. A plan re-run can therefore reuse
speech artifacts even when its plan ID or trace changes. Missing or corrupt
sidecars cause only the affected segment to render again.

## UtterPlan persistence and semantic capacity

Readio targets UtterPlan 0.4 schema v4. Current project plans are stored as canonical `.utterplan.toml` artifacts and loaded through `UtterancePlan.load()`. An indexed legacy `.utterplan.json` artifact is stale and actionable; Readio re-plans from its source instead of silently migrating that file.

During lowering, Readio rebases the public clause and parenthetical semantic boundaries once into request-local hints and retains linguistic token ranges independently of engine token support. These hints stay in `CapacityContext`; `SpeechRequest` and engine APIs remain UtterPlan-neutral.

For planned content, capacity fitting prefers clause boundaries, then parenthetical boundaries, newlines, conservative clause punctuation, linguistic-token edges, and whitespace. It does not rediscover sentence boundaries from periods or split arbitrary codepoints. Live/raw text keeps a distinct unplanned compatibility policy. Engine-reported measurements remain authoritative (including PocketSynth's model-token limit); Readio preserves exact text, never packs adjacent semantic sentences, and rebases child word timings to the original request. Atomic-lowering and capacity-fitting v2 manifests record split reason and boundary provenance.

## Engine boundary

Readio lowers each UtterPlan segment to a Readio-owned `SpeechRequest`; adapters receive requests and return `RenderedSpeech`. They do not receive UtterPlan documents or construct AudioCompose jobs. The engine runtime may perform engine-specific tokenization and acoustic inference, but semantic role binding, pause layout, markers, timing rebasing, timeline operations, and output remain Readio responsibilities.

A native adapter makes one strict synthesis call for a Readio-shaped request and never invokes the engine's convenience splitter. Readio owns capacity fitting: it measures when supported, handles typed too-long responses, and recursively subdivides exact text at legal sentence, clause, token, or word boundaries. Protected linguistic and pronunciation ranges are not cut. Child audio is merged and child-local timings are rebased to the original request.

Kokoro, Pocket, and Supertonic support request-scoped voice selection. Piper binds roles to voice-bundle targets. For target-bound execution, Readio validates every distinct target before opening sessions and reuses one session per target. Unsupported pronunciation overrides or other explicit semantics are rejected before runtime startup.

Engine adapters own canonical synthesis-profile identity. The common `speed` option is a synthesis control included in speech identity and forwarded only to the engine. Kokoro receives it directly; PiperSynth maps it to `length_scale = 1 / speed`; PocketSynth supports only `1.0`. Supertonic uses the validated model base language and forwards the speed multiplier to its atomic synthesis API. Composition rate remains separate, so synthesis speed is never applied twice.

## Managed and catalog voice sources

Supertonic discovery uses the engine's public model metadata API, while synthesis uses one atomic native request for each Readio request. Readio resolves locale tags to supported base languages and retains its ownership of text splitting and composition.

Pocket voices have three distinct sources: a predefined bundle voice, a local reference WAV identified by its content hash, or a managed prompt identified by its canonical reference, catalog SHA-256, source revision, and available provenance. `readio voices prompts` queries only PocketSynth's public prompt metadata API; it neither opens a Pocket model nor downloads prompt audio. The render plan carries the managed source as typed metadata rather than a semantic Readio voice reference. At synthesis time, Readio verifies the prepared voice provenance and records PocketSynth's normalized-audio fingerprint separately from the source hash. Managed prompts apply to the default synthesis voice and are not role bindings.

## Composition and status dependencies

Composition reads current segment keys and sidecars directly, without opening a
TTS engine. It orders each segment as:

```text
resolved pause_before -> canonical speech -> resolved pause_after
```

Pause durations are converted to frames once with `round(seconds * sample_rate)`.
The same frame value is used in the composition identity and the generated
silence source. Composition resolves semantic prosody to numeric AudioCompose
operations: `Tempo`, `PitchShift`, `Gain`, `FadeIn`, and `FadeOut`. It keeps
semantic labels out of AudioCompose. Final LUFS and true-peak policy are also
composition concerns.

The v2 composition identity includes ordered speech and silence layout, speech
hashes, audio digests, operation payloads, timing frames, and output policy.
Consequently:

- pause or prosody edits rebuild composition but do not rerun speech synthesis;
- text, pronunciation, token, voice, speed, voice-level, G2P, model, target revision, or
  calibration changes rerender only affected canonical segments;
- loudness changes rebuild composition only;
- codec changes rebuild export only;
- trace plan IDs and trace hashes are provenance, not cache validity checks.

Status derives synthesis currentness from current plan segment keys and valid
sidecars. Composition currentness is derived from the current layout and policy.
`compose` and preview can use the cache without a current-plan trace and do not
load a TTS engine.

## Streaming playback

`speak` and live playback use a persistent stateful `LayoutBuilder` and a Readio-owned bounded `sounddevice` queue. Each rendered segment is lowered and composed at the playback output rate, submitted before later segments are synthesized, then released promptly. Playback keeps pause, prosody, marker, and resampling state across chunks and disables whole-program mastering; file rendering remains the full-document mastered path.

## Input conversion and provenance

Source conversion has one boundary: the public `ssmdconvert` API. Auto filesystem documents are converted with SSMDConvert unless the file is already canonical SSMD. Explicit or in-memory Markdown is converted through SSMDConvert `convert_content` to canonical SSMD. Explicit literal text remains text and explicit SSMD remains SSMD. Converted `InputDocument` values retain `DocumentProvenance` (source format, media type, source name, converter/version, and metadata). PDF and DOCX ingestion requires the `readio[documents]` extra.

## Project role targets and resolution

New configuration and project records use schema 3. Global roles and project roles store the same structured `VoiceTarget`: canonical engine, voice ID, and optional target ID. Readio does not keep provider-specific runtime role rosters or project-wide provider selectors; SSMD's own `voice_bindings` namespace remains part of the external document format.

v0.3 config and project files require explicit `readio config migrate` or `readio project migrate PROJECT`; normal runtime code rejects obsolete structures instead of applying scattered fallbacks. Migration preserves a backup and fails on conflicting role targets. Document-level SSMD `voice_bindings` stays provider-namespaced as defined by SSMD; when one role appears in several namespaces, Readio reports ambiguity.

`readio plan roles` discovers references directly from editable SSMD scopes and reports per-scope locations and effective targets without requiring a generated plan index. The `--engine` inspection filter selects which effective targets to display; it does not override role resolution. Bindings are carried in the synthesis request and are never written into UtterPlan or SSMD source.

Binding precedence is `document > invocation CLI > project > global configured role > direct concrete voice`. A document binding remains authoritative per scope. Project role changes affect synthesis only, so they do not alter semantic `plan_id` or invalidate plan artifacts.

## Mixed-engine synthesis routing

For each selected segment, Readio resolves the symbolic role in that document scope to a `VoiceTarget`, then resolves the target engine adapter and engine selection. Unbound segments use the normal project synthesis selection. A project can therefore route one role through Kokoro and another through Piper, Pocket, or Kitten without selecting one project-wide provider or engine.

Every distinct engine-target route is validated before synthesis sessions open. Readio groups routed segments, opens one reusable session per target, and writes ordinary canonical speech artifacts consumed by composition. The semantic plan retains symbolic role references and remains independent of casting. Changing role bindings leaves the plan current and makes only synthesis and downstream stages stale; cached audio is retained.

Mixed-engine synthesis profiles use a deterministic, sorted set of route identities, per-scope role targets, and role-binding provenance. Speech cache identity includes the selected route, so changing a guest target does not invalidate unchanged host audio on another engine-target route. Status compares current role-target provenance with the active synthesis profile and reports `synthesis.stale.project_voice_bindings_changed` when bindings differ.

## End-to-end acceptance scenario

For a multi-sentence document, normal sentence topology produces one reusable
speech artifact per semantic segment. If only a sentence pause changes from
180 ms to 250 ms:

```text
SOURCE       current
DOCUMENT     current
PLAN         current
SYNTHESIS    current       # same segment speech hashes and sidecars
COMPOSITION  stale         # silence frame layout changed
OUTPUT       stale         # blocked by composition
```

Running `readio compose` rebuilds the timeline and master audio. No TTS model is
loaded. The synthesis trace may retain the previous plan ID as audit history,
while the cache remains reusable by current segment key and sidecar.
