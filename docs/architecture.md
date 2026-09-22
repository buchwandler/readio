# Readio architecture

Persistent projects separate semantic planning, canonical speech, editorial composition, and export:

```text
source/document
      |
      | semantic policy and linguistic enrichment
      v
UtterancePlan v2
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
       engine segment renderer
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

PyKokoro and PiperSynth retain their ordinary full-plan APIs for standalone
consumers. Readio uses their explicit prepared-segment APIs. Those APIs perform
model inference, contextual G2P, short-sentence handling, deterministic
model-specific cleanup, timestamps, and voice/model calibration, then stop at
canonical speech-only audio. They do not apply semantic pauses, presentation
pitch, user-facing rate or speed, volume, emphasis, fades, or final program
loudness.

Readio's engine adapter owns canonical profile identity. Project `speed` and
semantic rate are composition controls. Model-native controls such as a named
Kokoro model speed or Piper `length_scale` remain synthesis controls when
explicitly requested.

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
- text, pronunciation, token, voice, G2P, model, or calibration changes rerender
  only affected canonical segments;
- loudness changes rebuild composition only;
- codec changes rebuild export only;
- trace plan IDs and trace hashes are provenance, not cache validity checks.

Status derives synthesis currentness from current plan segment keys and valid
sidecars. Composition currentness is derived from the current layout and policy.
`compose` and preview can use the cache without a current-plan trace and do not
load a TTS engine.

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
