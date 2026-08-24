---
name: readio
description: Use the local readio CLI for exact reading, plain-text narration, SSMD-aware rendering, and explicit Spotify publishing.
compatibility: Requires the readio command and a configured local TTS backend. Spotify publishing additionally requires save-to-spotify and its authenticated session.
---

# Readio

Use Readio for local text to speech. It can play speech, render a bounded memory WAV, or publish a completed WAV through `save-to-spotify`. Plain text is the default input for summaries, recaps, explanations, implementation status, reading, and one-voice narration.

## Exact reading

When the user asks to read existing text, extract the exact requested text. Do not paraphrase or add an introduction.

```bash
readio speak <<'READIO_EOF'
<exact selected text>
READIO_EOF
```

For a local file:

```bash
readio speak --file path/to/document.md
readio speak --file path/to/document.md --select last-paragraph
readio speak --file path/to/document.md --select paragraph:3
```
For local Markdown files, pass the Markdown directly; Readio parses structure before synthesis and does not require manual extraction or rewriting as SSMD:

```bash
readio speak --file path/to/document.md
readio render --file docs/guide.md
```

For generated or piped Markdown, select the format explicitly:

```bash
readio render --input-format markdown <<'READIO_EOF'
# Summary

- First point
- Second point
READIO_EOF
```

Markdown headings, lists, links, images, code blocks, block quotes, tables, task lists, HTML text, and front matter are projected into speech-friendly text. Markdown styling does not create prosody. Use SSMD for explicit voices, rate, volume, pitch, breaks, markers, or multi-speaker structure. Use `--input-format text` to force literal reading of Markdown-looking content.


```bash
producer-command | readio speak --live
```
Markdown and SSMD live input are rejected because their complete-document structures cannot be parsed incrementally; use a complete non-live input instead.
## Narrated summaries and status

For a summary, recap, explanation, implementation status, or other one-voice narration, generate plain text and render it directly. Do not create an SSMD template just because the output is audio.

```bash
readio render <<'READIO_EOF'
<generated summary text>
READIO_EOF
```

Use an ingest artifact when the text should be retained or edited:

```bash
source="$(readio ingest new --name implementation-summary.txt)"
cat >"$source" <<'READIO_EOF'
<generated summary text>
READIO_EOF
readio render --file "$source"
```

## Use SSMD when semantics require it

Use SSMD for multiple speakers, dialogue, an explicitly requested podcast, logical roles, marks or chapters, or supported SSMD annotations and prosody. Preserve document bindings as authoritative and let Readio supply only missing configured defaults.

```bash
draft="$(readio template use podcast)"
readio ssmd check "$draft" --json
readio render --file "$draft"
```

When an SSMD file fails, run `readio ssmd check FILE --json`, inspect the diagnostic, and correct the source or configuration. Do not repeatedly guess at front matter or SSMD syntax. Do not silently downgrade explicitly requested multi-speaker output to one voice. Plain text is an acceptable fallback only when the user requested a narrated summary without multi-role semantics.

## Destinations

`readio render --file path` creates a WAV in the configured output directory when `-o` is omitted. The command prints the final path. Use `-o PATH` for an explicit destination and `--force` to replace an existing output.

`readio spotify --file path --title "Episode title"` publishes only when the user explicitly requests publishing. Without `--output`, Readio uses and deletes a secure temporary WAV. With `--output`, it retains the requested WAV.

## Local files, templates, and diagnostics

```bash
readio config init
readio template list
readio template validate --all
readio template show podcast
readio template use podcast
readio ingest path
readio ingest new --template briefing
readio ingest list
readio doctor
```

User templates live outside the installed package and can be modified directly. `readio config init --force` seeds only missing templates. Use `readio template reset NAME` or `readio template reset --all` to intentionally restore packaged defaults. `readio template validate` uses Readio consumer preflight by default; add `--roundtrip` for strict SSMD authoring checks.

## Publishing safety

Publishing is an external write action. Perform it only with explicit user intent. Do not inspect Spotify token files, call authentication commands, or send transcript text separately from requested media metadata. Report the returned Spotify URI and readiness state when waiting was requested.
