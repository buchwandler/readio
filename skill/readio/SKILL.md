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

PyKokoro >=0.9.5,<0.10 is the source of truth for runtime model and voice metadata. Agents should inspect JSON discovery rather than embedding model or voice inventories:

```bash
readio voices list --lang de --offline --json
readio voices show de-1 --offline --json
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
`--preference auto|github|huggingface|upstream` makes discovery views deterministic. `--model-source github|huggingface` controls the distribution used for discovery, validation, and runtime. Voices are model-scoped: the global reader voice is only a legacy fallback when no language/model selection changes the domain, and SSMD uses the resolved model roster. Use `readio doctor --json` for PyKokoro path/version/public-API mismatches.

Readio uses an explicit synthesis backend registry. PyKokoro is the implemented backend. Use `--engine BACKEND` to select a registered backend, and keep lexicon selectors such as `crane` separate from downstream asset IDs such as `de-de:crane`.
## Main production steps

```text
choose input -> plan -> review plan -> render -> optionally write manifest -> inspect artifact -> optionally publish
```

Always run `readio plan` (or `readio render --dry-run`) before rendering to see exactly
what synthesis values will be used. The plan shows model, voice, language, lexicons,
SSMD bindings, output format, and provenance — all without loading the TTS model.

The PyKokoro 0.9.5+ tokenizer controls are explicit plan inputs: `--g2p-fallback none|espeak|goruut` and `--lexicon-data-policy auto|installed-only`. Named selector `crane` is not the backend asset ID `de-de:crane`, and `de-crane` is a separate acoustic model ID. SSMD `language_detection` hints and the CLI `--language-detection`/`--detect-language` options are pronunciation-routing policy, not acoustic-language selection.

```bash
# Recommended: plan first
readio plan --file input.ssmd --format mp3 --json

# Then render if plan looks correct
readio render --file input.ssmd --format mp3
```

For an artifact that may be reused, published, compared, or handed to another agent, request durable evidence explicitly:

```bash
readio plan --file input.ssmd --format mp3 --json
readio render --file input.ssmd --format mp3 --manifest --json
```

The bounded render writes `<audio>.readio.json` beside the audio. The `readio.render-manifest.v1` sidecar contains the exact executed `readio.plan.v1`, a canonical plan digest, final encoded-file hash and byte count, audio summary, metadata, and assembled markers. Retain the audio path and manifest path as separate artifacts. `--manifest` is not available for live rendering, `speak`, planning, dry runs, or publishing.

Keep caller-requested output files. Readio owns and removes only generated temporary Spotify media; direct-upload inputs and caller-provided timeline files remain untouched.

## Command references

- [CLI and JSON](references/cli.md)
- [Input formats](references/input-formats.md)
- [SSMD and voice resolution](references/ssmd.md)
- [Spotify publishing](references/spotify.md)
- [Troubleshooting](references/troubleshooting.md)

## Safety boundary

Readio creates speech and may delegate completed media to `save-to-spotify`. It does not own Spotify authentication, token files, access tokens, destructive Spotify administration, or upstream TTS engine management. Advanced account operations remain explicit `save-to-spotify` operations outside this skill.
