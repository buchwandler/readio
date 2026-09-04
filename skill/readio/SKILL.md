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

PyKokoro 0.9.x is the source of truth for runtime model capabilities. Agents should inspect JSON discovery rather than embedding model or voice inventories:

```bash
readio models list --language de --offline --json
readio models show de-thorsten --offline --json
readio voices list --model de-thorsten --json
readio models list --preference huggingface --json
```

Persist user policy with the validated defaults workflow:

```bash
readio defaults set de --model de-thorsten --lexicon crane --offline --json
readio defaults show de --json
readio defaults show de-at --json
```

Use `--offline` for cache-only metadata and `--refresh` to update registry metadata only. `--offline --refresh` is invalid. Offline registry metadata and offline model synthesis are separate: synthesis also requires cached model and voice assets. `lexicons: null` means unknown capability; do not treat it as an empty list. Exact locale defaults override base-language defaults, and `--no-lexicons` clears inherited lexicons. Direct synthesis options override persisted defaults.
`--preference auto|github|huggingface|upstream` makes discovery views deterministic. `--model-source github|huggingface` controls the distribution used for discovery, validation, and runtime. Voices are model-scoped: the global reader voice is only a legacy fallback when no language/model selection changes the domain, and SSMD uses the resolved model roster. Use `readio doctor --json` for PyKokoro path/version/public-API mismatches.

## Main production steps

```text
choose input -> validate/preflight -> resolve voice/format -> render -> inspect artifact -> optionally publish
```

Keep caller-requested output files. Readio owns and removes only generated temporary Spotify media; direct-upload inputs and caller-provided timeline files remain untouched.

## Command references

- [CLI and JSON](references/cli.md)
- [Input formats](references/input-formats.md)
- [SSMD and voice resolution](references/ssmd.md)
- [Spotify publishing](references/spotify.md)
- [Troubleshooting](references/troubleshooting.md)

## Safety boundary

Readio creates speech and may delegate completed media to `save-to-spotify`. It does not own Spotify authentication, token files, access tokens, destructive Spotify administration, or upstream TTS engine management. Advanced account operations remain explicit `save-to-spotify` operations outside this skill.
