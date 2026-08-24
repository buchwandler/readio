# readio

`readio` is a terminal text to speech tool. It plays local speech with PyKokoro, renders bounded memory WAV files, and publishes completed audio through the external `save-to-spotify` CLI.

## Install

```bash
python -m pip install -e ".[cpu]"
```

For GPU ONNX Runtime:

```bash
python -m pip install -e ".[gpu]"
```

PyKokoro may download model and voice assets on first use. Spotify publishing requires the separately installed `save-to-spotify` executable and its authenticated session. Readio never reads Spotify credential files.

## Playback

```bash
readio speak "Hello from the terminal."
printf '%s\n' "Read this from stdin." | readio speak
readio speak --file notes.md              # parsed as Markdown
readio speak --file notes.md --input-format text  # literal text fallback
readio speak --file notes.md --select last-paragraph
readio speak --file notes.md --select paragraph:3
producer-command | readio speak --live
```
Readio parses `.md`, `.markdown`, `.mdown`, and `.mkd` as Markdown before synthesis. Headings, lists, links, images, code blocks, block quotes, tables, task lists, HTML text, and front matter are projected into speech-friendly text. Ordinary Markdown is isolated from SSMD controls; use `.ssmd` when explicit voices, rate, volume, pitch, breaks, or markers are required.

Markdown can also be supplied explicitly through stdin or literal input:

```bash
cat README.md | readio speak --input-format markdown
readio render --input-format markdown '# Title' 'This is **important**.'
```

Use `--input-format text` when a Markdown-looking file should be read as literal text.
## Configuration

Initialize one user-owned Readio configuration and its storage:

```bash
readio config init
readio config show
readio config validate
readio config set reader.voice bf_emma
readio config set voices.kokoro.roles.analyst am_michael
readio config set ssmd.voice_provider kokoro
```

The default configuration uses `platformdirs` for the config, template, ingest, and output locations. `READIO_CONFIG` overrides the config file path. Existing legacy files containing only `[reader]` continue to load and are upgraded to schema 1 when saved.

The configuration contains reader settings, SSMD defaults, provider-specific voice IDs, and logical role bindings. Templates refer to roles such as `host`, `analyst`, `guest`, and `narrator`, while ordinary literal text continues to use `reader.voice`.

## Templates

Built-in templates are copied into the user template directory during initialization. They are user-owned and are not overwritten by normal initialization or package upgrades.

```bash
readio template path
readio template list
readio template show podcast
readio template add custom --file custom.ssmd
readio template remove custom
readio template reset podcast
readio template reset --all
```

Create an agent-editable draft with an automatic filename:

```bash
draft="$(readio template use podcast)"
```

The returned path is under the configured ingest directory. A caller can request a filename with `readio template use podcast --name weekly-review.ssmd`.

## Ingest directory

The ingest directory stores text, Markdown, and SSMD files created for later processing.

```bash
readio ingest path
readio ingest new
readio ingest new --name notes.txt
readio ingest new --template podcast --name episode-42.ssmd
readio ingest list
```

Automatic names contain a UTC artifact ID such as `20260824T111423Z-5f8ab31c`. Explicit names are relative to the ingest directory and path traversal is rejected.

## Automatic WAV output

The output path is optional:

```bash
readio render "Hello from a file."
readio render --file "$draft"
readio render --file "$draft" -o episode.wav
```

Without `-o`, Readio uses the configured output directory. Generated ingest files retain their artifact stem in the WAV name. Other input files receive a new artifact suffix, and literal text uses the `readio` prefix. Automatic names never overwrite an existing file. Explicit output remains atomic and requires `--force` for replacement.

## SSMD consumption and authoring checks

For `.ssmd` inputs, Readio parses the document through SSMD 0.8.3 and passes a PyKokoro `SSMDRenderConfig` containing only missing Readio role defaults. Document `voice_bindings` remain authoritative. Normal `speak`, `render`, and `spotify` commands do not invoke `ssmd create`, rewrite the source, or require generic round-trip validation.

Inspect a document before rendering:

```bash
readio ssmd check episode.ssmd
readio ssmd check episode.ssmd --json
readio ssmd check episode.ssmd --roundtrip
```

`readio template validate --all` checks shipped or configured templates with the same consumer preflight. Add `--roundtrip` for strict SSMD authoring validation. Unknown logical roles fail before model inference with a Readio diagnostic.

## Spotify publishing

```bash
readio spotify --file "$draft" --title "Weekly Review" --wait
```

Publishing is explicit. Without `--output`, Readio renders to a secure temporary WAV and deletes it after upload or failure. With `--output`, it retains and uploads that file. Readio invokes `save-to-spotify --json` and does not inspect credentials or perform authentication.

## Doctor

```bash
readio doctor
```

Doctor is offline. It reports Readio configuration, configured directories and their existence, PyKokoro, SSMD module and executable availability, the selected provider, voice IDs, logical roles, sound dependencies, and `save-to-spotify`. It does not create directories, modify configuration, inspect credentials, or call the network.

## Agent Skill

The portable skill is in `skill/readio/SKILL.md`. It uses Readio templates and commands directly. It does not teach raw SSMD voice discovery, create, lint, temporary file management, or manual cleanup for normal podcast workflows.
