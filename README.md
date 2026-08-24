# readio

`readio` is a terminal text-to-speech tool that can play speech locally, render bounded-memory WAV files, or publish completed WAV media through the external `save-to-spotify` CLI. The current TTS backend is PyKokoro, but destinations depend only on Readio's rendered-audio boundary.

## Install

For CPU ONNX Runtime:

```bash
python -m pip install -e ".[cpu]"
```

For NVIDIA GPU ONNX Runtime:

```bash
python -m pip install -e ".[gpu]"
```

PyKokoro may download model and voice assets on first use. Spotify publishing additionally requires the separately installed `save-to-spotify` executable and an authenticated session managed by that tool. Readio does not read Spotify credentials or token files.

## Playback

```bash
readio speak "Hello from the terminal."
printf '%s\n' "Read this from stdin." | readio speak
readio speak --file notes.md
readio speak --file notes.md --select last-paragraph
readio speak --file notes.md --select paragraph:3
```

Selectors use PyKokoro's paragraph descriptors, so selection and synthesis use the same document segmentation.

## Live playback

```bash
producer-command | readio speak --live
```

A blank line closes a paragraph and starts its playback. `--live` reads stdin only and cannot be combined with `--file`, literal text, or a paragraph selector.

## WAV rendering

```bash
readio render "Hello from a file."
readio render --file episode.ssmd -o episode.wav
printf '%s\n' "Text from stdin." | readio render -o stdin.wav
producer-command | readio render --live -o live.wav
```

`readio render` writes PCM16 WAV audio incrementally instead of concatenating a whole document waveform in memory. The output is written to a temporary file in the destination directory and atomically replaces the requested path only after synthesis succeeds. Use `--force` to replace an existing output.

## SSMD

Readio passes SSMD documents to PyKokoro. It does not implement a second SSMD parser or validator. For a multi-speaker episode, author and validate the document with the SSMD CLI first:

```bash
ssmd --json voices list --provider kokoro
ssmd --json create draft.ssmd -o episode.ssmd --voice-provider kokoro --fail-on-warn
ssmd --json lint episode.ssmd --voice-provider kokoro --roundtrip --fail-on-warn
readio speak --file episode.ssmd
readio render --file episode.ssmd -o episode.wav
```

Portable logical roles can be bound in SSMD front matter:

```ssmd
---
title: Tech Talk
voice_bindings:
  kokoro:
    host: af_sarah
    guest: am_michael
---
<div voice="host">Welcome to Tech Talk.</div>
<div voice="guest">Today we are discussing portable SSMD documents.</div>
```

## Spotify publishing

`readio spotify` renders one WAV and passes that completed file to `save-to-spotify --json upload`. It does not stream raw audio to Spotify or implement authentication.

```bash
readio spotify \
  --file episode.ssmd \
  --title "My episode" \
  --show-id spotify:show:... \
  --wait
```

Other metadata options include `--new-show`, `--summary`, `--image`, and `--language`. `--show-id` and `--new-show` are mutually exclusive. `--title` is required. Use `--json` for one Readio-owned machine-readable result:

```json
{"ok": true, "episode_uri": "spotify:episode:...", "upload_status": "UPLOADING", "readiness": "READY", "audio_path": null}
```

Without `--output`, Readio uses a secure temporary WAV and deletes it after upload or failure. With `--output`, the WAV is retained and the same render is uploaded. `--wait` delegates readiness waiting to `save-to-spotify`; `--wait-timeout` is valid only with `--wait`.

Spotify live input is local incremental synthesis followed by WAV finalization and upload after stdin reaches EOF. It is not live Spotify publishing, and complete non-live SSMD input is preferred for globally prepared multi-speaker documents.

## Configuration and diagnostics

```bash
readio config init
readio config show
readio config set voice af_sarah
readio config set lang en-us
readio config set speed 1.15
readio doctor
```

`readio doctor` reports Readio, PyKokoro, sounddevice, soundfile, and whether `save-to-spotify` is on `PATH`. It never calls a token command or inspects credential files.

## Agent Skill

The portable skill is in `skill/readio/SKILL.md`. It distinguishes two workflows:

1. Exact reading. Extract the requested conversation text exactly and pipe it to `readio speak` without paraphrasing.
2. Derived podcast creation. Only when requested, draft and validate an SSMD episode, then choose `speak`, `render`, or `spotify` according to the user's destination intent.

Copy the skill into the skill directory used by your agent client, for example:

```bash
mkdir -p .agents/skills
cp -R skill/readio .agents/skills/readio
```

## Versioning

`readio` uses `setuptools-scm`. Package versions are derived from Git tags rather than hard-coded in source. The import package lives directly at `readio/`; there is intentionally no `src/` directory.
