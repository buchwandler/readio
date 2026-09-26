# Readio documentation

Readio is a terminal text-to-speech tool. It reads plain text or SSMD, resolves neutral synthesis requests through registered engines, composes audio timelines with AudioCompose, and publishes completed audio through `save-to-spotify`.

## Documentation map

```{toctree}
:maxdepth: 1

architecture
api
changelog
cli
incremental-rendering
projects
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

Engine packages may download model, voice, or bundle assets on first use. Spotify publishing additionally requires the separately installed and authenticated `save-to-spotify` executable.

## Quick start

Read literal text, a file, or standard input:

```bash
readio speak "Hello from the terminal."
readio speak --file notes.md
printf '%s\n' "Read this text." | readio speak
```

Render an audio file instead of playing audio:

```bash
readio render --file notes.md -o notes.wav
readio render --file notes.md -o notes.mp3
readio render --file notes.md --format m4a
readio render --file notes.md --format ogg
```

Render also supports PCM16 FLAC and Opus; `.ogg` is Ogg/Vorbis and `.opus` is separate:

```bash
readio render --file notes.md -o notes.flac
readio render --file notes.md -o notes.opus
```

WAV is the default. An output suffix selects the encoder, while `--format` selects the automatic output suffix or can be combined with a matching explicit suffix. Extensionless output is normalized to the selected format. M4A and Opus require an `ffmpeg` executable on `PATH`; WAV/FLAC use PCM16, while MP3 and Ogg/Vorbis require matching SoundFile/libsndfile codec support.

With no explicit output path, Readio writes a uniquely named file below the configured output directory. Existing files are not overwritten unless `--force` is supplied for an explicit path.

## Persistent project exports

Generic project export supports WAV, FLAC, MP3, M4A, Ogg/Vorbis, and Opus. `.ogg` stays Vorbis; `.opus` is a separate format. Readio defaults M4A to 192k and Opus to 96k; those are Readio defaults and do not assert TTSForge parity.

```bash
readio export novel.readio --format flac
readio export novel.readio --format opus --bitrate 96k
readio audiobook export novel.readio --format m4b --cover cover.jpg
```

M4B is available only for audiobook projects through `readio audiobook export`. It muxes AAC audio and embedded chapters; title/author default from project metadata and cover art is explicit-only (JPEG/PNG). Readio's M4B AAC default is 192k.

Markdown is a first-class input format. Files ending in `.md`, `.markdown`, `.mdown`, or `.mkd` are parsed before synthesis; `.ssmd.md` is detected as SSMD before its `.md` suffix. Use `--input-format markdown` for Markdown from stdin or literal text:

```bash
readio speak --file README.md
readio render --file docs/design.md
cat README.md | readio speak --input-format markdown
```

Headings, lists, links, images, code blocks, block quotes, tables, task lists, HTML text, and front matter become speech-friendly text. Markdown styling does not create SSMD prosody. Use `.ssmd` or `.ssmd.md` for explicit voices, rate, volume, pitch, breaks, or markers; use `--input-format text` to force literal reading of a Markdown-looking file.

## Input and rendering

The `speak`, `render`, and `spotify` commands accept the same input forms:

- Positional text, joined with spaces; exactly one existing regular file token is loaded as a file.
- `--file PATH` for the unambiguous scripting form and UTF-8 text, Markdown, or SSMD.
- Standard input when no positional text or file is provided.
- `--live` for incremental standard-input playback or rendering. Blank lines close live paragraphs.

A missing path-like positional token is an error rather than literal speech. Use `--input-format text` when an existing filename must be spoken literally; explicit Markdown and SSMD formats still support positional file detection.

For non-live input, `--select` can be `all`, `last-paragraph`, or `paragraph:N`. The default synthesis unit is controlled by `reader.unit` and can be overridden with `--unit sentence` or `--unit paragraph`.

Synthesis options are available on all three commands:

````text
--voice VOICE             engine voice ID
--voice-file PATH         PocketSynth reference WAV
--engine ENGINE           pykokoro, piper, or pocket
--model TARGET            model ID, Piper voice bundle, or Pocket bundle
--precision int8|fp32     PocketSynth bundle precision
--temperature FLOAT       PocketSynth generation temperature
--lsd-steps INT           PocketSynth latent diffusion steps
--max-frames INT          PocketSynth maximum generated frames
--frames-after-eos INT    PocketSynth frames after end of sequence
--lang LANGUAGE           language code, such as en-us
--lexicon NAME            named lexicon when supported by the engine
--no-lexicons             disable engine lexicons when supported
--auto-lexicons            use automatic engine lexicons when supported
--g2p-fallback MODE       none, espeak, or goruut where supported
--lexicon-data-policy     auto or installed-only where supported
--language-detection      off or auto where supported
--detect-language LANG    repeatable pronunciation-routing hint
--speed NUMBER            speech speed multiplier
--voice-level MODE       off or calibrated voice-level handling
--spacy MODE              linguistic analysis policy
--short-sentence MODE     short-sentence handling policy
--pause-mode MODE         auto, tts, or manual
--unit UNIT               sentence or paragraph

Readio requires SSMD >=0.9,<0.10 and UtterPlan >=0.3,<0.4, persisting linguistic artifacts as UtterPlan schema v3 inside `readio.plan.v2`. Supported optional engine floors are PyKokoro >=0.10.0,<0.11, PiperSynth >=0.2.0,<0.3, and PocketSynth >=0.2.0,<0.3. The `kokoro`, `piper`, and `pocket` extras install these runtimes. `readio doctor` checks their strict request APIs; incompatible packages do not trigger fallback to retired pipeline paths.
Readio's built-in `pause_mode` is `auto`; an explicit `[reader] pause_mode` setting or `--pause-mode tts|manual|auto` override takes precedence.

Speed is an engine synthesis multiplier, not a composition tempo. PyKokoro receives the value directly, PiperSynth uses its reciprocal as `length_scale`, and PocketSynth rejects explicit values other than `1.0`.

Readio, not the engine adapter, owns text-capacity fitting and exact-text subdivision. Adapters synthesize one strict request at a time and do not call native splitters. Readio preserves legal linguistic and pronunciation boundaries and fails when an oversized request has no legal split.

```bash
readio models list --language de --offline
readio models show de-thorsten --offline
readio voices list --model de-thorsten --json
readio models list --preference huggingface --json
readio models show de-thorsten --preference github --json
readio defaults set de --model de-thorsten --lexicon crane --offline
readio defaults show de-at --json
readio render --lang de --file notes.md
readio lexicons list --lang de --offline --json
readio lexicons show crane --lang de --offline --json
````

`models`, `voices`, and `lexicons` enumerate targets from the unified engine registry. They are metadata-only and do not load model weights or instantiate ONNX runtimes. Offline mode uses cached catalogs; refresh updates catalog metadata only.
`--model-source` applies only to engines that advertise distribution-source selection. Voice rosters are target-scoped where the engine exposes them, and lexicons are listed only for engines that support lexicon discovery. SSMD preflight validates role targets against the selected engine catalog.
Readio uses `readio.engines` as its sole engine registry. The registered engines are `pykokoro`, `piper`, and `pocket`; aliases are normalized before target resolution. Lexicon operations reject engines that do not advertise lexicon support.

## Synthesis planning

Non-live rendering is plan-first: `readio render` resolves one `readio.plan.v2` execution plan and then executes exactly that plan. Use `readio render --dry-run` to display the one-shot plan without loading TTS:

```bash
readio render --file notes.md --lang de --format mp3 --dry-run --json
readio render --file notes.md --dry-run
```

Keep the layers separate:

- **Discovery** (`readio models`, `readio voices`, `readio lexicons`) enumerates what engines registered in `readio.engines` provide.
- **Defaults** (`readio defaults`) persist validated per-language preferences.
- **Planning** (`readio render --dry-run`) resolves one concrete request, including engine, target, source, quality, voice, lexicons, SSMD role bindings, output format/backend/path, and provenance. Generated output paths are allocated once by the plan and reused by the render.
- **Project planning** (`readio plan`) builds engine-free semantic Utterplan artifacts and manages project-local SSMD roles. It does not resolve one-shot engine, model, or output choices.
- **Render result** executes the plan; a plan that fails validation (for example `model_language_incompatible`, `model_runtime_unavailable`, `ssmd_unresolved_voice`, `encoder_unavailable`) is printed with its diagnostics and no TTS model is loaded.

Plans preserve engine-neutral render targets, request options, SSMD role bindings, and pronunciation-routing hints. Lexicon behavior is supplied only by engines that advertise lexicon support; unsupported explicit pronunciation semantics produce diagnostics before runtime startup.

Planning policy stores UtterPlan linguistic options separately from engine render controls. Readio compiles one semantic plan, lowers its segments to requests, then delegates acoustic synthesis to the selected engine without passing the UtterPlan object to adapters.
UtterPlan receives `synthesis.spacy` and `synthesis.short_sentence` as typed linguistic policy. Readio records those settings in the semantic plan; an engine adapter maps them only when the selected published engine API supports them.

One-shot planning is deterministic: `--resolve-voices` is rejected by `render --dry-run`; use `--voice-bind ROLE=VOICE_ID` for an invocation or `readio plan bind ROLE VOICE` for a project setting.

## Durable render manifests

Use `--manifest` when a bounded render produces an artifact that needs durable, machine-readable evidence:

```bash
readio render --file notes.md --format mp3 --dry-run --json
readio render --file notes.md --format mp3 --manifest
```

The successful render writes `<audio>.readio.json` beside the audio. Its `readio.render-manifest.v1` payload embeds the exact executed `readio.plan.v2`, a canonical plan digest, the final encoded-file hash and byte count, `RenderSummary` audio facts, document metadata, and assembled marker offsets. Planning describes intended execution; the manifest describes the completed artifact.

The option is explicit and applies only to bounded `render`. It is rejected with `--live` and does not create manifests for `speak`, `plan`, dry runs, or publishing. Human output remains the audio path. JSON output remains one object and adds `manifest` with the sidecar path, or `null` without the option.

## Render progress

`render` uses a dependency-free progress reporter on stderr. In default `auto` mode it is enabled only for an interactive stderr; `--progress` forces it and `--no-progress` disables it. `--json` keeps stdout to exactly one result object, disables automatic progress, and still permits explicit stderr progress:

```bash
readio render --file notes.md -o notes.mp3 --progress
readio render --file notes.md -o notes.mp3 --json --progress
```

Bounded renders show phases, completed/total units, percentage, elapsed time, approximate ETA, generated audio duration, and finalization. Live rendering shows elapsed time, cumulative units, and audio duration but no invented percentage or ETA.

## Verbose diagnostics

Use `-v` for timestamped INFO lifecycle records and `-vv` for DEBUG details from Readio and the selected engine:

```bash
readio -v speak "Hello"
readio -vv render episode.ssmd -o episode.mp3
```

The option is global and may appear before or after a command. Logs are written only to stderr. Human results and JSON remain on stdout, so `readio -v doctor --json` still emits valid JSON. `--progress` and `--no-progress` remain independent; verbose mode makes progress line-oriented on a TTY. Review paths, model names, and voice identifiers before sharing diagnostics. Complete document text, raw audio, Spotify credentials, and authorization responses are not logged.
Playback-only options are `--queue-size` and `--device`. Audio rendering is streamed to an atomic output file through a bounded audio path rather than accumulated as one in-memory waveform.

## Configuration

Initialize and inspect the user-owned configuration:

```bash
readio config init
readio config path
readio config show
readio config validate
readio config set reader.pause_mode auto
```

`READIO_CONFIG` overrides the default configuration file path. Configuration is TOML with schema 2; schema-0/1 files remain readable and are upgraded when saved. The main sections are:

- `[reader]`: `voice`, `lang`, `speed`, `pause_mode`, `unit`, `queue_size`, `device`, `spacy`, and `short_sentence`.
- `[ssmd]`: the selected `voice_provider` and SSMD validation behavior.
- `[paths]`: user template, ingest, and audio output directories.
- `[voices.<provider>]`: concrete voice IDs and logical role mappings.
- `[languages.<locale>]`: validated model, source, quality, voice, ordered lexicons, `g2p_fallback`, and `lexicon_data_policy` defaults.
- `[reader]`: optional `language_detection` mode and ordered `detect_languages` routing hints.
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

Files ending in `.ssmd` or `.ssmd.md` are parsed as SSMD. Readio runs consumer preflight before rendering when `ssmd.validate_before_render` is enabled:

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
readio spotify publish --file episode.ssmd --title "Weekly Review" --format mp3 --wait
readio spotify upload recording.m4a --title "Lecture 3" --show-id spotify:show:abc --wait 2m
readio spotify shows --json
readio spotify status spotify:episode:abc --wait
readio spotify doctor --json
```

`spotify publish` renders source and delegates the completed media to `save-to-spotify`; `spotify upload` accepts existing caller-owned WAV, MP3, M4A, or OGG without rendering or deleting it. Without `--output`, publish's generated media is temporary and cleaned up; explicit output is retained. `--timeline FILE` passes through a caller-owned JSON object, while `--chapters-from-markers` generates Readio chapters; they are mutually exclusive and both wait for READY before setting the timeline.

Use `--json` for machine-readable output. Global `--json` is accepted before or after the command. `--wait` optionally takes a duration and `--wait-timeout` is deprecated; `--api-timeout` is separate from readiness waiting.

Readio does not read Spotify credential files or perform authentication. It delegates external writes and account setup to `save-to-spotify`.

## Diagnostics and development

Run the offline environment check with:

```bash
readio doctor
```

The local doctor is human-readable by default and supports `readio doctor --json`. It reports configuration, directories, dependencies, format availability, and the upstream executable/version probe without authentication or token access. Use `readio spotify doctor` for the explicit external integration check.
If an engine reports `request API compatible: no`, run `readio doctor --json` to inspect the installed package version and adapter status. The runtime does not fall back to a legacy pipeline when the published API is incompatible.

Run the test suite and lint checks from a development checkout:

```bash
pytest
ruff check .
```

The main execution path is `readio/cli.py`. Project SSMD role discovery and binding settings live in `readio/project_roles.py` and `readio/project_settings.py`; project synthesis orchestration is in `readio/stages/synthesis.py`. Input normalization is in `readio/document.py`, configuration in `readio/config.py`, audio sinks in `readio/audio.py` and `readio/wave.py`, and external Spotify integration in `readio/spotify.py`.

## SSMD voice resolution

SSMD document bindings use `voice_bindings.PROVIDER.ROLE: CONCRETE_VOICE_ID` and remain authoritative. Inspect configured voices and persisted role mappings with:

````bash
readio voices list --lang de --json
readio voices show de-ko-3 --json
readio roles list --provider kokoro

For a selected model, inspect concrete voices with `readio voices list --model MODEL --lang LANG --json`; stable selectors are engine-qualified lookup aliases (`en_us-ko-4` -> `af_heart` on Kokoro v1.0, `de-ko-3`, `de-pi-9`), while `--lang en-us` is a locale filter and bindings remain canonical concrete voice IDs. Selector identities come from the authoritative registry rather than Readio-owned numbering. Document bindings take precedence over invocation bindings, which take precedence over configured portable roles. Readio rejects a concrete target outside the active model roster and lists the valid voices.
Use `readio roles bind ROLE VOICE_ID` for an explicit persistent mapping. For automation, pass missing logical roles only for one invocation:

```bash
readio render --file episode.ssmd \
  --voice-bind moderator=af_sarah \
  --voice-bind architect=am_michael
````

`--resolve-voices` is an explicit interactive convenience. It prompts once per unique missing role only on a usable TTY and never persists choices. JSON and non-TTY execution never prompts. `readio ssmd bind FILE --voice-bind ROLE=VOICE_ID -o OUTPUT.ssmd` is the explicit source-materialization workflow; ordinary `speak`, `render`, and `spotify` commands do not mutate SSMD.
