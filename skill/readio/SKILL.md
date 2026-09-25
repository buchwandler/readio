---
name: readio
description: Use the local readio CLI for exact reading, plain-text narration, SSMD-aware rendering, and explicit Spotify publishing.
compatibility: Requires the readio command and a configured local TTS backend. Spotify publishing additionally requires save-to-spotify and its authenticated session.
---

# Readio

Use Readio for local text to speech, bounded-memory audio rendering, and explicit publication of completed media. Readio supports WAV, MP3, M4A, and OGG output. Keep the core workflow deterministic and non-interactive for agents.

## Workflow decisions

1. **Exact reading:** When asked to read existing text, preserve the requested text and use `readio speak`; do not paraphrase or add an introduction.
2. **Narrated summaries:** Generate plain text and render it directly. Do not create SSMD unless voice, role, prosody, markers, or multi-speaker semantics are required.
3. **Markdown:** Pass Markdown directly to Readio; it projects structure into speech-friendly text. Use `--input-format text` only when Markdown-looking input must be read literally.
4. **SSMD:** Use SSMD for multiple speakers, explicit roles, prosody, markers, or chapters. Run deterministic `readio ssmd check FILE --json` before rendering and resolve voices with repeatable `--voice-bind` options.
5. **Publishing:** Spotify is an external write. Publish only after explicit user intent; never infer permission from a request merely to render audio.
6. **Agents:** Prefer complete files, explicit output paths, `--json`, and non-interactive voice bindings. Never rely on `--resolve-voices` outside an interactive human TTY.

## Model discovery and defaults

Supported optional runtime floors are PyKokoro >=0.10.0,<0.11, PiperSynth >=0.2.0,<0.3, and PocketSynth >=0.2.0,<0.3. Each registered engine is the source of truth for its own models, voices, and capability metadata; use `readio doctor --json` to inspect API compatibility.

```bash
readio voices list --lang de --offline --json
readio voices show de-ko-3 --offline --json
readio models show de-thorsten --offline --json
```

readio lexicons list --lang de --offline --json
readio lexicons show crane --lang de --offline --json

Persist user policy with the validated defaults workflow:

```bash
readio defaults set de --model de-thorsten --lexicon crane --offline --json
readio defaults show de --json
readio defaults show de-at --json
```

Use `--offline` for cache-only metadata and `--refresh` to update registry metadata only. `lexicons: null` means automatic/unknown capability depending on the payload; in synthesis plans, `null` means PyKokoro language defaults and `[]` means explicit provider-only pronunciation. Exact locale defaults override base-language defaults. Use `--no-lexicons` for `()`, `--auto-lexicons` for `None`, and repeat `--lexicon` to preserve ordered layers.
Use `--offline` for cache-only metadata and `--refresh` to update registry metadata only. `lexicons: null` means automatic/unknown capability depending on the payload, while `[]` is an explicit empty selection where supported. Exact locale defaults override base-language defaults. Use `--no-lexicons` for the empty selection, `--auto-lexicons` to restore defaults, and repeat `--lexicon` to preserve ordered layers.

`--preference auto|github|huggingface|upstream` makes discovery views deterministic. `--model-source github|huggingface` applies only to engines that advertise distribution-source selection. Voices are model-scoped where supported; use `readio doctor --json` for installed engine API/version diagnostics.

Readio's engine registry provides PyKokoro, PiperSynth, and PocketSynth through one neutral request API. Use `--engine BACKEND` to choose an installed backend, and inspect each engine's own model and voice catalog rather than assuming the packages share inventory semantics.

## Main production steps

```text
choose input -> render --dry-run -> review execution plan -> render -> optionally write manifest -> inspect artifact -> optionally publish
```

Use `readio render --dry-run` to inspect one-shot synthesis values before rendering. It shows model, voice, language, lexicons, SSMD decisions, output format, and provenance without loading TTS. `readio plan` is reserved for persistent project build and role-management commands.

PyKokoro 0.10 tokenizer controls are explicit plan inputs: `--g2p-fallback none|espeak|goruut` and `--lexicon-data-policy auto|installed-only`. Named selector `crane` is not the backend asset ID `de-de:crane`, and `de-crane` is a separate acoustic model ID. SSMD `language_detection` hints and the CLI `--language-detection`/`--detect-language` options are pronunciation-routing policy, not acoustic-language selection.

The common `--speed` option changes synthesis speed, not composition rate. PyKokoro receives it directly, PiperSynth maps it to reciprocal `length_scale`, and PocketSynth supports only `1.0`. `--voice-level off|calibrated` selects the engine's voice-level mode. Readio owns exact-text capacity fitting and subdivision; adapters do not call native text splitters.

```bash
# Recommended: inspect one-shot plan first
readio render --file input.ssmd --format mp3 --dry-run --json

# Then render if plan looks correct
readio render --file input.ssmd --format mp3
```

For an artifact that may be reused, published, compared, or handed to another agent, request durable evidence explicitly:

```bash
readio render --file input.ssmd --format mp3 --dry-run --json
readio render --file input.ssmd --format mp3 --manifest --json
```

The bounded render writes `<audio>.readio.json` beside the audio. The `readio.render-manifest.v1` sidecar contains the exact executed `readio.plan.v2`, a canonical plan digest, final encoded-file hash and byte count, audio summary, metadata, and assembled markers. Retain the audio path and manifest path as separate artifacts. `--manifest` is not available for live rendering, `speak`, planning, dry runs, or publishing.

Keep caller-requested output files. Readio owns and removes only generated temporary Spotify media; direct-upload inputs and caller-provided timeline files remain untouched.

## Persistent project roles

Use `readio plan` only for persistent project planning and role settings. Inspect SSMD roles before generating the semantic plan, then bind a reusable project-local voice:

```bash
readio project init episode.ssmd -o episode.readio
cd episode.readio
readio plan roles
readio plan bind narrator en_us-ko-4
readio plan
readio synth
```

Project bindings are distinct from portable SSMD front matter, user-global `readio roles bind`, and invocation-only `--voice-bind`. Resolution precedence is document, invocation, project, global configured role, then direct voice. Project binding changes do not rewrite source or change Utterplan identity. They make synthesis stale without invalidating the plan; `readio status` reports the change and recommends `readio synth`. Use `readio render --dry-run` for one-shot execution planning.

## Command references

- [CLI and JSON](references/cli.md)
- [Input formats](references/input-formats.md)
- [SSMD and voice resolution](references/ssmd.md)
- [Spotify publishing](references/spotify.md)
- [Troubleshooting](references/troubleshooting.md)

## Safety boundary

Readio creates speech and may delegate completed media to `save-to-spotify`. It does not own Spotify authentication, token files, access tokens, destructive Spotify administration, or upstream TTS engine management. Advanced account operations remain explicit `save-to-spotify` operations outside this skill.
