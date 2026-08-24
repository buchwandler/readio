# Readio documentation

Readio is a terminal text-to-speech tool. It reads plain text or SSMD documents with PyKokoro, plays speech locally, renders WAV files, and can publish completed audio through `save-to-spotify`.

## Documentation map

```{toctree}
:maxdepth: 1

changelog
```

- [Project README](https://github.com/buchwandler/readio/blob/main/README.md), quick-start commands and feature overview.
- [Agent Skill](https://github.com/buchwandler/readio/blob/main/skill/readio/SKILL.md), instructions for using Readio from an agent workflow.

## Installation

Readio requires Python 3.10 or newer.

```bash
python -m pip install -e ".[cpu]"
```

Use the GPU extra when a GPU-enabled ONNX Runtime is available:

```bash
python -m pip install -e ".[gpu]"
```

PyKokoro may download model and voice assets the first time it is used. Spotify publishing additionally requires the separately installed and authenticated `save-to-spotify` executable.

## Quick start

Read literal text, a file, or standard input:

```bash
readio speak "Hello from the terminal."
readio speak --file notes.md
printf '%s\n' "Read this text." | readio speak
```

Render a WAV file instead of playing audio:

```bash
readio render --file notes.md -o notes.wav
```

With no explicit output path, Readio writes a uniquely named WAV file below the configured output directory. Existing files are not overwritten unless `--force` is supplied for an explicit path.

Markdown is a first-class input format. Files ending in `.md`, `.markdown`, `.mdown`, or `.mkd` are parsed before synthesis; use `--input-format markdown` for Markdown from stdin or literal text:

```bash
readio speak --file README.md
readio render --file docs/design.md
cat README.md | readio speak --input-format markdown
```

Headings, lists, links, images, code blocks, block quotes, tables, task lists, HTML text, and front matter become speech-friendly text. Markdown styling does not create SSMD prosody. Use `.ssmd` for explicit voices, rate, volume, pitch, breaks, or markers; use `--input-format text` to force literal reading of a Markdown-looking file.

## Input and rendering

The `speak`, `render`, and `spotify` commands accept the same input forms:

- Positional text, joined with spaces.
- `--file PATH` for UTF-8 text or SSMD.
- Standard input when no positional text or file is provided.
- `--live` for incremental standard-input playback or rendering. Blank lines close live paragraphs.

For non-live input, `--select` can be `all`, `last-paragraph`, or `paragraph:N`. The default synthesis unit is controlled by `reader.unit` and can be overridden with `--unit sentence` or `--unit paragraph`.

Synthesis options are available on all three commands:

```text
--voice VOICE       PyKokoro voice ID
--lang LANGUAGE     language code, such as en-us
--speed NUMBER      speech speed multiplier
--pause-mode MODE   tts, manual, or auto
--unit UNIT         sentence or paragraph
```

Playback-only options are `--queue-size` and `--device`. WAV rendering is streamed to an atomic output file through a bounded audio path rather than accumulated as one in-memory waveform.

## Configuration

Initialize and inspect the user-owned configuration:

```bash
readio config init
readio config path
readio config show
readio config validate
```

`READIO_CONFIG` overrides the default configuration file path. Configuration is TOML with schema 1. The main sections are:

- `[reader]`: `voice`, `lang`, `speed`, `pause_mode`, `unit`, `queue_size`, and `device`.
- `[ssmd]`: the selected `voice_provider` and SSMD validation behavior.
- `[paths]`: user template, ingest, and WAV output directories.
- `[voices.<provider>]`: concrete voice IDs and logical role mappings.

Set values with dotted keys. Aliases `voice`, `lang`, and `speed` target the corresponding reader settings:

```bash
readio config set reader.voice bf_emma
readio config set voices.kokoro.roles.analyst am_michael
readio config set ssmd.voice_provider kokoro
```

The default provider is `kokoro`. Built-in logical roles include `narrator`, `host`, `analyst`, and `guest`. A configured role must resolve to one of the provider's configured voice IDs.

## Templates and ingest files

Templates are copied from the package into the user template directory during initialization. User copies are not overwritten by normal initialization.

```bash
readio template list
readio template show podcast
readio template validate --all
readio template use podcast --name weekly-review.ssmd
```

Use `--roundtrip` with template validation for strict SSMD authoring checks. `template reset NAME` restores a packaged template intentionally.

Ingest files are retained in the configured ingest directory for later editing or rendering:

```bash
readio ingest path
readio ingest new --name notes.txt
readio ingest new --template podcast --name weekly-review.ssmd
readio ingest list
```

Automatic artifact names contain a UTC timestamp and random suffix. Explicit ingest names must be single filenames below the ingest directory.

## SSMD documents

Files ending in `.ssmd` are parsed as SSMD. Readio runs consumer preflight before rendering when `ssmd.validate_before_render` is enabled:

```bash
readio ssmd check episode.ssmd
readio ssmd check episode.ssmd --json
readio ssmd check episode.ssmd --roundtrip
```

Document-local `voice_bindings` are authoritative. Readio supplies only missing defaults from the selected provider's configured roles. A voice reference must resolve to a document binding, configured logical role, or configured concrete voice ID. Unresolved references fail before model inference.

Plain text remains the default for one-voice narration. Use SSMD when the document needs multiple speakers, logical roles, marks, or chapters.

## Spotify publishing

Publishing is explicit:

```bash
readio spotify --file episode.ssmd --title "Weekly Review" --wait
```

The command renders a WAV and invokes `save-to-spotify --json`. Without `--output`, the WAV is temporary and is deleted after the operation. With `--output`, the requested WAV is retained. `--show-id` and `--new-show` select the destination show, while `--summary`, `--image`, and `--language` set episode metadata.

Use `--json` for a machine-readable result. `--wait` waits for readiness, and `--wait-timeout` sets the readiness timeout. `--chapters-from-markers` converts SSMD markers into a Spotify timeline and requires the episode to reach `READY`.

Readio does not read Spotify credential files or perform authentication. It delegates the external write operation to `save-to-spotify`.

## Diagnostics and development

Run the offline environment check with:

```bash
readio doctor
```

Doctor reports configuration, configured directories, PyKokoro, SSMD, the selected provider and voices, sound dependencies, and `save-to-spotify` availability. It does not create directories, modify configuration, inspect credentials, or call the network.

Run the test suite and lint checks from a development checkout:

```bash
pytest
ruff check .
```

The main execution path is `readio/cli.py`. Input normalization is in `readio/document.py`, configuration in `readio/config.py`, synthesis orchestration in `readio/reader.py`, audio sinks in `readio/audio.py` and `readio/wave.py`, and external Spotify integration in `readio/spotify.py`.
