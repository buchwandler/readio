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

PyKokoro and Pocket support request-scoped voice selection. Piper binds roles to voice-bundle targets. For target-bound execution, Readio validates every distinct target before opening sessions and reuses one session per target. Unsupported pronunciation overrides or other explicit semantics are rejected before runtime startup.

Engine adapters own canonical synthesis-profile identity. The common `speed` option is a synthesis control included in speech identity and forwarded only to the engine. PyKokoro receives it directly; PiperSynth maps it to `length_scale = 1 / speed`; PocketSynth supports only `1.0`. Composition rate remains separate, so synthesis speed is never applied twice.

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

## Project voice bindings

Project-local logical-role assignments live in `project.json` at `settings.ssmd.voice_bindings`, keyed by provider and role. `settings.ssmd.voice_provider` optionally selects the active provider. When it is absent, Readio infers a provider only if exactly one non-empty binding namespace exists. Projects with no project bindings retain the global configuration fallback; multiple namespaces without an active provider are ambiguous. `readio plan roles`, `bind`, `unbind`, and synthesis use the same effective provider. Binding a stable selector stores its canonical voice and activates that selector's provider without modifying global configuration.

`readio plan roles` discovers references directly from editable SSMD scopes and reports per-scope locations and effective sources without requiring a generated plan index. The shared synthesis request attaches project bindings as a distinct resolution layer; bindings are never written into UtterPlan or the SSMD source.

The binding precedence is `document > invocation CLI > project > global configured role > direct concrete voice`. Document bindings remain authoritative per scope. Concrete project choices affect synthesis only, so changing them does not alter semantic `plan_id` or invalidate plan artifacts.

With no explicit engine, project synthesis selects the engine associated with the effective project provider and does not inherit global `reader.engine` or `reader.voice`. An explicit `readio synth --engine ...` chooses a run-local engine/provider and never mutates project settings.

The synthesis profile records provider, sorted project bindings, and a SHA-256 provenance hash under `project_voice_bindings`. Grouped target-route profiles use the v3 canonical identity with target selections, per-scope bindings, and the project-binding fingerprint. Status compares current project settings with recorded provenance and reports `synthesis.stale.project_voice_bindings_changed` on mismatch. Plan remains current, synthesis becomes stale, composition and output are blocked downstream, and `readio synth` is the next action. Cached audio is not deleted.

## Engine voice-binding modes

Engine capabilities declare whether voice selection is request-scoped or target-scoped. A request-scoped engine can switch a voice on each `SpeechRequest`; a target-scoped engine requires Readio to resolve roles to distinct `SynthesisTarget`s. Pocket reference voices are explicit content-addressed voice sources, not entries in a global named-voice catalog.
For target-bound execution, Readio resolves every speech segment's symbolic role for its document scope, validates all distinct target selections before opening any session, and groups segments by target. It opens one reusable session per distinct target, not one model per segment. The semantic plan remains unchanged and retains symbolic role references. Aggregate v3 synthesis profiles identify the targets, per-scope bindings, and project-binding fingerprint; progress events include target IDs and report target-specific model loading.

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
