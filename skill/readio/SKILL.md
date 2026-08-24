---
name: readio
description: Read existing text exactly or create a validated SSMD narrated episode with the local readio CLI. Use for playback, bounded WAV rendering, or explicit Spotify publishing. The current backend is PyKokoro, and Spotify authentication belongs to the external save-to-spotify executable.
compatibility: Requires the readio command and a configured TTS backend. Spotify publishing additionally requires save-to-spotify and its existing authenticated session.
---

# Readio

Use the local `readio` command for local text-to-speech. Readio can play speech, render a bounded-memory WAV, or publish a completed WAV through `save-to-spotify`.

## Mode A: exact reading

Use this mode when the user asks to read, speak, replay, or narrate existing text. Extract exactly the requested text from the current agent context. Do not paraphrase, summarize, or add an introduction. Readio cannot inspect the conversation history itself.

```bash
readio speak <<'READIO_EOF'
<exact selected conversation text>
READIO_EOF
```

For a local UTF-8 text, Markdown, or SSMD file:

```bash
readio speak --file path/to/document.md
readio speak --file path/to/document.md --select last-paragraph
readio speak --file path/to/document.md --select paragraph:3
```

When the user means the last paragraph of the agent's previous response, extract only that paragraph from the conversation and pipe it to Readio. When the user means a file paragraph, use the file selector. Do not include code fences, citations, tool traces, or unrelated context unless requested.

For incremental local playback:

```bash
producer-command | readio speak --live
```

A blank line closes a paragraph and begins playback before stdin reaches EOF. `--live` accepts stdin only. It cannot be combined with literal text, `--file`, or a paragraph selector.

## Mode B: create a podcast or narrated adaptation

Use this mode only when the user asks for a transformation, such as:

- make this conversation a podcast
- turn this discussion into an audio episode
- create a narrated recap and save it to Spotify

The derived script may reorganize the source, but must not introduce unsupported factual claims unless the user requested external research. Use stable logical roles such as `host`, `analyst`, and `guest`.

### Authoring gate

Readio consumes SSMD. It does not author, parse, or validate SSMD itself. Discover available Kokoro voices and validate the generated document with the SSMD CLI before invoking Readio:

```bash
ssmd --json voices list --provider kokoro
ssmd --json create "$draft" -o "$episode" \
  --voice-provider kokoro \
  --fail-on-warn
ssmd --json lint "$episode" \
  --voice-provider kokoro \
  --roundtrip \
  --fail-on-warn
```

Use SSMD front matter for portable metadata and logical voice bindings. Do not guess voices when the SSMD CLI can list them.

### Choose the destination

- Hear it now: `readio speak --file "$episode"`
- Create a local file: `readio render --file "$episode" -o episode.wav`
- Publish it: `readio spotify --file "$episode" --title "Episode title"`

Publishing is an external write action. Perform it only when the user's request explicitly includes saving, uploading, or publishing intent. Report the returned episode URI after a successful upload. If waiting was requested, report the readiness state too.

### Podcast workflow example

```bash
draft="$(mktemp "${TMPDIR:-/tmp}/readio-podcast.XXXXXX.ssmd")"
episode="$(mktemp "${TMPDIR:-/tmp}/readio-podcast-final.XXXXXX.ssmd")"

cat >"$draft" <<'SSMD'
# Episode title

<div voice="host">
...
</div>

<div voice="analyst">
...
</div>
SSMD

ssmd --json create "$draft" -o "$episode" \
  --voice-provider kokoro \
  --fail-on-warn
ssmd --json lint "$episode" \
  --voice-provider kokoro \
  --roundtrip \
  --fail-on-warn

readio spotify \
  --file "$episode" \
  --title "Episode title" \
  --wait

rm -f "$draft" "$episode"
```

Do not hard-code a Spotify show URI. Pass `--show-id` only when the user names a show. Otherwise preserve `save-to-spotify upload` default-show behavior. Use `--new-show` only when explicitly requested.

## Readio commands

### WAV rendering

```bash
readio render --file episode.ssmd -o episode.wav
printf '%s\n' "Text from stdin." | readio render -o stdin.wav
producer-command | readio render --live -o live.wav
```

WAV rendering streams each rendered unit directly to a PCM16 WAV. It does not concatenate a whole document waveform in memory. Persistent output is atomic and requires `--force` to replace an existing path.

### Spotify publishing

```bash
readio spotify \
  --file episode.ssmd \
  --title "Episode title" \
  --show-id spotify:show:... \
  --wait
```

`save-to-spotify` is an external executable. Readio invokes it with `--json`, reuses its authenticated session, and never reads token files, prints bearer tokens, or calls a token-printing command. Without `--output`, Readio deletes its secure temporary WAV after upload or failure. With `--output`, it preserves and uploads the same WAV.

`readio spotify --live` synthesizes paragraphs incrementally but cannot upload until stdin ends and the WAV is finalized. This is not live Spotify publishing. For multi-speaker SSMD with front matter and logical voice bindings, prefer complete non-live input because live paragraphs are prepared independently.

### Diagnostics

```bash
readio doctor
```

Doctor reports local dependency and executable presence, including `save-to-spotify`, without network access or credential inspection.

## Safety and privacy

- Do not send transcript or SSMD text to Spotify separately from the generated media metadata explicitly requested by the user.
- Use secure temporary files for generated SSMD and temporary WAV artifacts.
- Remove temporary artifacts when the workflow completes where the host environment permits.
- Never copy, inspect, or log Spotify token files.
