# readio

`readio` is a terminal text to speech tool. It plays local speech with PyKokoro, renders bounded memory WAV, MP3, M4A, or OGG files, and publishes completed audio through the external `save-to-spotify` CLI.

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

The default configuration uses `platformdirs` for the config, template, ingest, and output locations. `READIO_CONFIG` overrides the config file path. Existing legacy files containing only `[reader]` continue to load and are upgraded to schema 2 when saved.

The configuration contains reader settings, SSMD defaults, provider-specific voice IDs, and logical role bindings. Templates refer to roles such as `host`, `analyst`, `guest`, and `narrator`, while ordinary literal text continues to use `reader.voice`.

### Model discovery and language defaults

PyKokoro 0.9.x is the runtime contract and owns the model, language, voice, quality, frontend, and named-lexicon catalog. Discovery is metadata-only and does not download model weights:

```bash
readio models list --language de --offline
readio models show de-thorsten --offline
readio voices list --model de-thorsten --json
readio models list --preference huggingface --json
readio voices list --model de-thorsten --preference github --json
```

Use `--refresh` to refresh registry metadata only. `--offline --refresh` is invalid. Offline metadata requires a cached registry; offline synthesis additionally requires cached model and voice assets.

Persist a validated default per language. Language keys are normalized, and locale-specific profiles fall back to their base language:

```bash
readio defaults set de --model de-thorsten --lexicon crane --offline
readio defaults show de --json
readio defaults show de-at --json
readio render --lang de --file notes.md
```

When a model is selected, Readio fills its normalized source, default voice, and preferred quality, then validates language compatibility, voice roster, quality, named lexicons, and experimental frontend permission before saving. `--no-lexicons` clears an inherited explicit selection. Repeat `--lexicon` to preserve ordered layered lookup.

Direct `speak`, `render`, and `spotify publish` options (`--model`, `--model-source`, `--quality`, repeatable `--lexicon`, `--no-lexicons`, and `--allow-experimental`) override persisted defaults. Use `--json` for automation; JSON preserves unknown lexicon capability as `null` rather than an empty list.
`--model-source github|huggingface` selects the same distribution for discovery, validation, and runtime construction. Voices are model-scoped: the legacy global `reader.voice` is retained only for unchanged default-reader use; `--lang de` without a voice leaves PyKokoro free to choose the German model default.

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

## Multi-format audio output

The output path is optional and WAV remains the default:

```bash
readio render "Hello from a file."
readio render --file "$draft" -o episode.wav
readio render "Hello" -o episode.mp3
readio render --file episode.ssmd --format m4a
readio render "Hello" --format ogg
```

## Render progress

`render` and `spotify` report low-noise rendering progress on stderr when stderr is an interactive terminal:

```bash
readio render --file episode.ssmd -o episode.mp3 --progress
readio render --file episode.ssmd -o episode.mp3 --no-progress
readio render --file episode.ssmd -o episode.mp3 --json
readio spotify publish --file episode.ssmd --title "Episode" --json
readio --json spotify status spotify:episode:abc --wait
```

Progress includes the current phase, completed units, elapsed time, approximate ETA for bounded renders, generated audio duration, and finalization. Live renders show cumulative units without a percentage or ETA. `--json` keeps stdout to one result object; automatic progress is disabled in JSON mode, while explicit `--progress` remains stderr-only.
When `-o` is supplied, its `.wav`, `.mp3`, `.m4a`, or `.ogg` suffix selects the encoder. Use `--format` when the output path is omitted or to select the automatic filename suffix. An explicit format and suffix must agree. Extensionless output paths receive the selected suffix, and unsupported suffixes fail before synthesis. Automatic names use the configured output directory and never overwrite an existing file. Explicit output remains atomic and requires `--force` for replacement.

M4A output requires an `ffmpeg` executable on `PATH`. WAV uses PCM16, while MP3 and OGG use the installed SoundFile/libsndfile codecs.

## SSMD consumption and authoring checks

For `.ssmd` inputs, Readio parses the document through SSMD 0.8.6 and passes a PyKokoro 0.9 `SSMDRenderConfig` containing only missing Readio role defaults. Document `voice_bindings` remain authoritative, invocation `--voice-bind` values override configured provider roles, and concrete targets must belong to the active model roster. Normal `speak`, `render`, and `spotify` commands do not invoke `ssmd create`, rewrite the source, or require generic round-trip validation.

Inspect a document before rendering:

```bash
readio ssmd check episode.ssmd
readio ssmd check episode.ssmd --json
readio ssmd check episode.ssmd --roundtrip
```

`readio template validate --all` checks shipped or configured templates with the same consumer preflight. Add `--roundtrip` for strict SSMD authoring validation. Unknown logical roles fail before model inference with a Readio diagnostic.

## Spotify publishing

Publishing is explicit and uses the clean command family:

```bash
readio spotify publish --file "$draft" --title "Weekly Review" --format mp3 --wait
readio spotify upload recording.m4a --title "Lecture 3" --show-id spotify:show:abc --wait 2m
readio spotify shows --json
readio spotify status spotify:episode:abc --wait
readio spotify doctor --json
```

Publish renders and uploads Readio source. Direct upload starts from caller-owned WAV, MP3, M4A, or OGG and never deletes or overwrites it. Without `--output`, generated publish media is temporary and deleted after success or failure; with `--output`, it is retained. `--chapters-from-markers` and caller-owned `--timeline FILE` are mutually exclusive, and either timeline path waits for READY before publishing. `--wait` optionally accepts a duration; `--wait-timeout` is deprecated. `--api-timeout` controls an upstream request separately from readiness waiting.

Readio invokes `save-to-spotify --json`, reports its detected version in diagnostics, and does not inspect credentials, expose tokens, or perform authentication.

## Doctor

```bash
readio doctor
```

Doctor is offline/local by default and supports `readio doctor --json`. It reports Readio configuration, directories, TTS/SSMD dependencies, audio formats, and the upstream `save-to-spotify` path/version probe. It does not authenticate, inspect credentials, or perform Spotify network operations; use `readio spotify doctor` for the explicit external integration check.

## Agent Skill

The portable skill is in `skill/readio/SKILL.md`. It uses Readio templates and commands directly. It does not teach raw SSMD voice discovery, create, lint, temporary file management, or manual cleanup for normal podcast workflows.

## SSMD voice resolution

Use document-local bindings when a portable SSMD file should carry its speaker choices:

```yaml
voice_bindings:
  kokoro:
    moderator: af_sarah
    architect: am_michael
```

Discover configured IDs and persisted roles with `readio voices list --json` and `readio voices roles`. Persist a reusable missing-role mapping with `readio voices bind ROLE VOICE_ID`. For deterministic one-run automation, use repeatable options:
Runtime voice inventories are model-specific. Use `readio voices list --model MODEL --json` for concrete IDs; a configured portable role such as `host` must be bound to a voice supported by the selected model. Readio reports the active model and valid voices before inference when a binding is incompatible.

```bash
readio render --file episode.ssmd \
  --voice-bind moderator=af_sarah \
  --voice-bind architect=am_michael
```

`--resolve-voices` prompts only when explicitly requested from an interactive TTY. It never persists choices. JSON, agents, scripts, and non-TTY execution must use `--voice-bind` instead. Document bindings remain authoritative, and unresolved roles are reported before TTS or external publishing work begins. `readio ssmd bind FILE --voice-bind ROLE=VOICE_ID -o OUTPUT.ssmd` explicitly materializes bindings into a new source file; ordinary consumption never edits SSMD.
