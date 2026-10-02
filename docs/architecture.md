# Readio architecture

Persistent projects separate semantic planning, canonical speech, editorial composition, and export:

```text
source/document
      |
      | semantic policy and linguistic enrichment
      v
UtterancePlan schema v3
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

## Semantic plan and speech cache

UtterPlan remains the owner of semantic identity. Its unit hashes may include
resolved pauses and directives because those facts describe the semantic plan.
Readio does not use a unit hash as its acoustic cache atom. Unit selectors are
expanded to ordered, unique `PlanSegment` IDs before synthesis.

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

## Engine boundary

Readio lowers each UtterPlan segment to a Readio-owned `SpeechRequest`; adapters receive requests and return `RenderedSpeech`. They do not receive UtterPlan documents or construct AudioCompose jobs. The engine runtime may perform engine-specific tokenization and acoustic inference, but semantic role binding, pause layout, markers, timing rebasing, timeline operations, and output remain Readio responsibilities.

A native adapter makes one strict synthesis call for a Readio-shaped request and never invokes the engine's convenience splitter. Readio owns capacity fitting: it measures when supported, handles typed too-long responses, and recursively subdivides exact text at legal sentence, clause, token, or word boundaries. Protected linguistic and pronunciation ranges are not cut. Child audio is merged and child-local timings are rebased to the original request.

Kokoro and Pocket support request-scoped voice selection. Piper binds roles to voice-bundle targets. For target-bound execution, Readio validates every distinct target before opening sessions and reuses one session per target. Unsupported pronunciation overrides or other explicit semantics are rejected before runtime startup.

Engine adapters own canonical synthesis-profile identity. The common `speed` option is a synthesis control included in speech identity and forwarded only to the engine. Kokoro receives it directly; PiperSynth maps it to `length_scale = 1 / speed`; PocketSynth supports only `1.0`. Composition rate remains separate, so synthesis speed is never applied twice.

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

Automatic source conversion has one boundary: the public `ssmdconvert` API. Normal auto-converted `InputDocument` values retain `DocumentProvenance` (source format, media type, source name, converter/version, and metadata). Explicit text, Markdown, and SSMD input is not labeled as a converter result.

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
