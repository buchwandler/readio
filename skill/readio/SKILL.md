---
name: readio
description: Read text aloud through the local readio CLI. The current backend is PyKokoro. Use when the user asks to read, speak, narrate, or replay text from the conversation or a local text/Markdown file, including requests such as "read the last paragraph" or "read paragraph 3". Do not assume the CLI can inspect the LLM transcript; pass the exact conversation text that should be spoken.
compatibility: Requires the `readio` command and a configured TTS backend. The current implementation uses PyKokoro, an ONNX Runtime provider, and sounddevice audio output.
---

# Readio

Use the local `readio` command to speak text. The command performs local TTS; do not replace the user's request with a written paraphrase when they explicitly asked to hear text.

## Core rules

1. Determine exactly which text the user wants spoken from the current agent context.
2. If the target text is in the conversation, pass that exact text to `readio speak` on stdin. The CLI does not have access to the conversation history.
3. If the target is a local UTF-8 text or Markdown file, use `--file` and, when useful, a deterministic `--select` selector.
4. Do not include code fences, citations, tool traces, or unrelated surrounding text unless the user asked for them to be read too.
5. Prefer the persistent configured voice/language/speed. Override them only when the user asks.

## Commands

Read exact text from agent context:

```bash
readio speak <<'READIO_EOF'
<exact text selected from the conversation>
READIO_EOF
```

Read the last paragraph of a file using PyKokoro's paragraph segmentation:

```bash
readio speak --file path/to/document.md --select last-paragraph
```

Read paragraph 3 of a file (1-based):

```bash
readio speak --file path/to/document.md --select paragraph:3
```

Read a live text stream. A blank line closes a paragraph and allows playback to start before EOF:

```bash
producer-command | readio speak --live
```

Inspect or change persistent settings:

```bash
readio config show
readio config set voice af_sarah
readio config set lang en-us
readio config set speed 1.15
```

Check the local runtime when playback fails:

```bash
readio doctor
```

## "Read the last paragraph" decision

- If the user means the last paragraph of the agent's own previous response, extract only that paragraph from the conversation and pipe it to `readio speak`.
- If the user means the last paragraph of a file, run `readio speak --file FILE --select last-paragraph`.
- If the reference is genuinely ambiguous, use the nearest clearly referenced text rather than sending a large transcript.
