# readio

`readio` is a terminal text-to-speech tool. It plans speech with UtterPlan, resolves engine targets, and owns segment synthesis orchestration, AudioCompose timelines, and output. It plays local speech, renders WAV, FLAC, MP3, M4A, Ogg/Vorbis, or Opus files, exports audiobook M4B with embedded chapters, and can publish completed audio through the external `save-to-spotify` CLI.

## Install

```bash
python -m pip install "readio[kokoro,cpu]"
```

For GPU ONNX Runtime:

```bash
python -m pip install "readio[kokoro,gpu]"
```

For the other engines, install `readio[piper,cpu]`, `readio[pocket]`, or `readio[kitten]`. `readio[all,cpu]` installs all supported engine packages and optional spaCy planning support for a CPU environment.

Engine adapters may download target and voice assets on first use. Spotify publishing requires the separately installed `save-to-spotify` executable and its authenticated session. Readio never reads Spotify credential files.

### Optional spaCy linguistic planning

Install the optional Utterplan spaCy support when local grammatical annotations are desired:

```bash
python -m pip install "readio[spacy]"
```

Install a compatible local spaCy language model separately. Readio never downloads models implicitly. The default `reader.spacy = "auto"` tries the best locally installed model and falls back to Utterplan's analyzer when none is available. The explicit `sm`, `md`, `lg`, and `trf` settings require the selected local model tier and fail if it is unavailable. `off` disables spaCy analysis.

`readio plan` stores token annotations and linguistic provenance in the Utterplan schema v3 artifact. Rendering an existing project plan consumes those stored annotations and does not rerun spaCy when the engine, voice, or acoustic settings change. Direct one-shot commands such as `readio speak` may use the selected engine's local frontend because they do not consume a persisted semantic plan.

`readio plan build` reports planning phases on interactive terminals; force this with `--progress` or suppress it with `--no-progress`. Progress is written to stderr, so JSON stdout stays machine-readable. The phase details can identify a spaCy model and linguistic-analysis passes; the existing `reader.spacy` configuration still controls analysis. See the [CLI guide](docs/cli.md#planning-progress).

## Python API

Use `readio.api` from Python applications for typed synchronous speech, project, catalog, configuration, and diagnostics services. Start with the [Python API guide](docs/api.md) and executable [planning example](examples/python_api.py).

## CLI help and discovery

```bash
readio
readio --help
readio COMMAND --help
readio engines
readio formats
```

Running `readio` without arguments shows the command overview. `readio engines` lists known synthesis engines, and `readio formats` lists generic audio and audiobook output formats. See [CLI reference](docs/cli.md) for help behavior and examples.

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

`speak` submits synthesized segments to Readio's bounded audio-device queue as they are produced; playback does not wait for the whole document and does not apply whole-program mastering. File rendering remains a separate full-document path that keeps mastering. Both paths share speech synthesis and composition semantics, while Readio owns playback setup and queueing.

A single existing positional token is also treated as a file path by `speak`, `render`, and `spotify publish`, including `.ssmd`, `.ssmd.md`, and Markdown files:

```bash
readio speak README.md
readio render episode.ssmd -o episode.mp3
readio spotify publish episode.ssmd --title "Episode"
```

For scripts, prefer the explicit `--file PATH` form. A missing path-like token fails instead of being spoken as a filename. Use `--input-format text` to force an existing filename to remain literal text.

In auto mode, filesystem documents are converted to canonical SSMD by `ssmdconvert` before synthesis. This includes supported text, Markdown, HTML, PDF, DOCX, EPUB, and SSMD files. Explicit `--input-format text`, `markdown`, or `ssmd` forces textual interpretation instead of conversion. Headings, lists, links, images, code blocks, block quotes, tables, task lists, HTML text, and front matter are projected according to the converter; ordinary Markdown remains isolated from SSMD controls.

Auto-converted `InputDocument` values retain public `DocumentProvenance`: source format, media type, source name, converter and version, and converter metadata. Explicit text, Markdown, or SSMD input is not mislabeled as converted.

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
readio roles bind analyst am_michael --engine kokoro
readio config set reader.pause_mode auto
```

The default configuration uses `platformdirs` for config, template, ingest, and output locations. `READIO_CONFIG` overrides the configuration path. New files use schema 3 and canonical engine IDs. v0.3 configuration requires the explicit `readio config migrate` command; Readio does not silently apply legacy provider fallbacks.

Schema-3 configuration contains reader settings, SSMD validation, paths, language policies, and engine-qualified global role bindings. A role target stores its canonical `engine`, `voice`, and optional `target_id`; provider-specific voice tables are not runtime configuration. Run `readio config migrate` to convert a v0.3 config explicitly. Templates refer to roles such as `host`, `analyst`, `guest`, and `narrator`, while literal narration may use `reader.voice`.
Project manifests also use schema 3. Before opening a v0.3 project, run `readio project migrate PROJECT`; both migration commands preserve a backup and stop on conflicts rather than guessing.

### Model discovery and language defaults

Readio uses one registry for `kokoro`, `piper`, `pocket`, `supertonic`, and `kitten`; model, voice, and lexicon discovery use the selected engine's catalog and capabilities. Run `readio doctor` to check whether an engine package and its required public API are available.

Supported engine package floors are PyKokoro >=0.10.2,<0.11, PiperSynth >=0.2.1,<0.3, PocketSynth >=0.2.3,<0.3, SupertonicSynth >=0.1.2,<0.2, and KittenSynth >=0.1.1,<0.2. Each engine package owns its own runtime dependencies; Readio does not require OnnxVoice. Install Kokoro or Piper with a runtime extra such as `readio[kokoro,cpu]` or `readio[piper,cpu]`, and install Pocket, Supertonic, and Kitten with `readio[pocket]`, `readio[supertonic]`, or `readio[kitten]`. PDF and DOCX ingestion requires the `readio[documents]` extra. `readio[all,cpu]` also installs optional spaCy support.

`readio voices list` shows runnable voices with semantic references. Canonical engine IDs are `kokoro`, `piper`, `pocket`, `supertonic`, and `kitten`; upstream package names such as `pykokoro`, `pipersynth`, and `supertonicsynth` are accepted only as input aliases where applicable. Filters use `--engine`, `--model`, `--lang`, and `--gender`.

Voice references use `SYSTEM:TARGET[/VOICE]`: `kokoro:v1.0/af_heart`, `piper:en_US-amy-medium`, `pocket:english_2026-04/alba`, or `supertonic:supertonic-3/F1`. The system and target identify the engine and model/bundle; the voice suffix is omitted when the target itself is the voice. `readio voices show REF` inspects one reference. Native voice IDs can be used when discovery context resolves them uniquely; supply engine or target context where the command supports it. References identify voices, while `--lang` and `--gender` filter their descriptive metadata.

Kitten can be inspected and selected without changing the other engine workflows:

```bash
readio voices list --engine kitten
readio speak --engine kitten --model nano-0.8-int8 --voice Jasper "Hello"
```

Supertonic targets are discovered from its model catalog. Readio maps locale tags to the engine's base-language key, so `en-us` is synthesized as `en`; unsupported language bases are rejected.

```bash
readio voices list --engine supertonic --lang en-us
readio render --engine supertonic --model supertonic-3 --voice F1 --lang en-us "Hello"
```

A Pocket bundle advertising generic `en` can satisfy `--lang en-us`; a bundle explicitly advertising `en-GB` does not. Generic language metadata stays generic and is not assigned unsupported locale specificity.
Common `--speed` is a positive synthesis multiplier, not a composition tempo: Kokoro receives it directly and PiperSynth converts it to `length_scale = 1 / speed`. PocketSynth supports only `1.0`; other explicit values fail validation. Supertonic forwards the multiplier to its atomic synthesis API. `--voice-level off|calibrated` selects the engine's voice-level handling and participates in speech identity.

Adapters make one strict native synthesis request for each Readio-shaped child and do not invoke native text splitters. Readio owns capacity measurement and exact-text subdivision, preserves linguistic and pronunciation boundaries, and merges child audio and local timings. Unsupported explicit semantics and unsplittable requests fail with stable Readio errors instead of being discarded or truncated.

```bash
readio models list --language de --offline
readio models show de-thorsten --offline
readio voices list --model de-thorsten --json
readio models list --preference huggingface --json
readio voices list --model de-thorsten --preference github --json
readio lexicons list --lang de --offline --json
readio lexicons show crane --lang de --offline --json
```

Piper voice bundles are discovered through PiperSynth without loading ONNX during planning:

```bash
readio voices list --engine piper --lang de
readio voices list --model piper --lang de
readio voices list --engine piper --model de_DE-thorsten-medium --lang de
readio render --engine piper --voice de_DE-thorsten-medium --lang de --dry-run --json "Hallo Welt"
readio speak --engine piper --voice de_DE-thorsten-medium --lang de "Hallo Welt"
readio render --engine piper --voice de_DE-thorsten-medium --lang de --manifest -o article.wav "Hallo Welt"
readio render --engine pipersynth --voice de_DE-thorsten-medium --lang de --dry-run --json "Hallo Welt"
```

Pocket targets are bundle IDs. `--voice` selects a bundle's predefined voice, `--voice-file` uses a local reference WAV, and `--voice-prompt` selects a managed Kyutai prompt. These selectors are mutually exclusive. Tune only the Pocket-specific generation controls you need:

```bash
readio voices list --engine pocket --lang en-us
readio render --engine pocket --model BUNDLE_ID --voice VOICE --precision int8 "Hello"
readio render --engine pocket --model BUNDLE_ID --voice-file reference.wav --temperature 0.6 --lsd-steps 3 "Hello"
readio voices prompts --engine pocket --dataset alba
readio voices prompts --engine pocket --variant casual --license cc-by-4.0
readio render --engine pocket --model BUNDLE_ID --voice-prompt kyutai-tts-voices:alba-mackenna/casual --temperature 0.6
```

Local reference voice files are user or project assets. Readio records their content hash in `readio.plan.v2`; local paths are not included in acoustic render identity. Managed Pocket prompts are catalog assets: plans pin the prompt reference, SHA-256, source revision, and available provenance. Pocket verifies prepared provenance and records its normalized-audio fingerprint separately from the source hash. This selector applies to the default synthesis voice, not per-role bindings. Piper's published text API does not accept Readio token or pronunciation annotations, so explicit pronunciation directives are diagnosed instead of silently discarded.

Use `--speaker NAME_OR_ID` for a multi-speaker Piper bundle. Live rendering depends on the selected engine's declared capability.

Use `--refresh` to refresh registry metadata only. `--offline --refresh` is invalid. Offline metadata requires a cached registry; offline synthesis additionally requires cached model and voice assets.

Persist a validated default per language. Language keys are normalized, and locale-specific profiles fall back to their base language:

```bash
readio defaults set de --model de-thorsten --lexicon crane --offline
readio defaults show de --json
readio defaults show de-at --json
readio render --lang de --file notes.md
```

When a model is selected, Readio fills its normalized source, default voice, and preferred quality, then validates language compatibility, voice roster, quality, named lexicons, and experimental frontend permission before saving. `--no-lexicons` selects explicit provider-only pronunciation (`lexicons=[]`); `--auto-lexicons` returns to engine language defaults (`lexicons=null`). Repeat `--lexicon` to preserve ordered layered lookup.
Readio owns pause placement. Its `pause_mode` defaults to `auto`; `tts` leaves natural sentence timing to the engine, while `manual` uses explicit semantic boundary pauses. A persisted `reader.pause_mode` remains the default for that installation.
Named lexicons use engine selectors, not backend asset IDs:
crane = named selection token
de-de:crane = language-qualified Lexphon asset resolved downstream
de-crane = separate acoustic model ID

````

For reproducible German synthesis:

```bash
readio models show de-thorsten
readio defaults set de --model de-thorsten --lexicon crane --g2p-fallback espeak --lexicon-data-policy auto
readio render --file article.md --lang de --dry-run --json
readio render --file article.md --lang de --model de-thorsten --no-lexicons --g2p-fallback espeak --format mp3
````

The plan also records optional PyKokoro language detection. SSMD documents may use `language_detection: {mode: auto, languages: [de, en]}`; this routes pronunciation fragments while retaining the selected acoustic language.

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

## Standalone LLM authoring guides

`llm-guides/ssmd/` contains standalone Markdown instructions for generic LLMs. These guides are distinct from the runtime `.ssmd` templates managed by `readio template`.

No Readio or Python installation is needed on the authoring system: choose one guide and attach it with the task and source material. When the harness supports artifacts, ask it to create and return one downloadable `.ssmd.md` file; otherwise save the raw SSMD response as a `.ssmd.md` file. Readio also accepts `.ssmd` for compatibility. Rendering and final validation happen later on a system where Readio is installed.

See [`llm-guides/README.md`](llm-guides/README.md) for the catalog and the authoring-to-rendering workflow.

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

## Planning before rendering

Every non-live `readio render` resolves an explicit synthesis plan before TTS work. Use `readio render --dry-run` to inspect the same plan without loading a model; a normal render executes exactly the plan it resolved:

```bash
readio render --file episode.ssmd --format mp3 --dry-run --json
readio render --file episode.ssmd --dry-run          # human output
readio render --file episode.ssmd                    # resolves the same plan, then executes it
```

Planning, discovery, defaults, and render results are distinct layers:

- **`readio models` / `readio voices` (discovery)** list what the installed engine runtime _could_ provide — model IDs, languages, voices, qualities, lexicons, status.
- **`readio defaults` (defaults)** persist validated per-language policy that resolution _prefers_.
- **`readio plan` (project planning)** builds semantic plans for persistent projects and provides `roles`, `bind`, and `unbind` for project-local SSMD cast settings. It does not resolve one-shot engine/model/output choices or load TTS.
- **`readio render` (render result)** executes the plan; the plan JSON's synthesis, SSMD bindings, and output path are the values actually used. A render that fails planning exits 1 with the plan and its diagnostics instead of loading TTS.

`readio.plan.v2` JSON exposes `schema`, `ok`, `input`, `planning`, `semantic_plan`, `render` (with engine-neutral target), `output` (format, encoder backend, path, path origin, `force`), `environment` (generic package versions), `decisions` (winning source per field), and `diagnostics`:

```json
{
  "schema": "readio.plan.v2",
  "ok": true,
  "operation": "render",
  "input": {
    "source_path": "article.md",
    "source_kind": "file",
    "requested_format": "markdown",
    "format": "markdown",
    "source_sha256": "...",
    "selector": "all"
  },
  "planning": { "language": "de", "unit": "sentence", "pause_mode": "auto" },
  "semantic_plan": {
    "format": "utterplan",
    "schema_version": 3,
    "plan_id": "...",
    "sha256": "...",
    "path": "plan/document.utterplan.json"
  },
  "render": {
    "engine": "piper",
    "target": {
      "id": "de_DE-thorsten-medium",
      "language": "de",
      "voice": "thorsten"
    },
    "rate": 1.0,
    "options": { "ssmd_voice_bindings": { "narrator": "thorsten" } }
  },
  "output": {
    "mode": "file",
    "format": "mp3",
    "encoder_backend": "soundfile",
    "path": ".../article.mp3",
    "path_origin": "explicit",
    "force": false
  },
  "environment": { "packages": {}, "ffmpeg_available": true },
  "decisions": [
    {
      "field": "ssmd.bindings.narrator",
      "origin": "cli",
      "locator": "request.voice_bindings"
    }
  ],
  "diagnostics": []
}
```

One-shot planning is deterministic and non-interactive. Use repeatable `--voice-bind ROLE=VOICE_ID` options for invocation-only choices or persist project-local role choices with `readio plan bind`; `--resolve-voices` is rejected by `render --dry-run`.

## Durable render manifests

For bounded renders that will be reused, published, compared, or handed to another agent, request an opt-in post-render manifest:

```bash
readio render --file episode.ssmd --format mp3 --dry-run --json
readio render --file episode.ssmd --format mp3 --manifest
readio render --file episode.ssmd --format mp3 --manifest --json
```

A successful render writes the audio and a colocated `<audio>.readio.json` sidecar. The sidecar uses schema `readio.render-manifest.v2` and records the exact executed `readio.plan.v2`, its canonical SHA-256, the semantic plan identity, the final encoded audio hash and byte count, render summary facts, document metadata, and final marker offsets. The plan is pre-execution intent; the manifest is post-execution evidence.

Human stdout remains only the audio path. JSON render output remains one object and adds `manifest` with the sidecar schema and path, or `null` when the flag is absent. `--manifest` is available only for bounded rendering, not `--live`, `speak`, planning, dry runs, or publishing. If sidecar writing fails, Readio preserves the committed audio and returns `render.manifest_error`.

## Multi-format audio output

The output path is optional and WAV remains the default:

```bash
readio render "Hello from a file."
readio render --file "$draft" -o episode.wav
readio render "Hello" -o episode.mp3
readio render --file episode.ssmd --format m4a
readio render "Hello" --format ogg
```

Project exports support lossless FLAC and Opus in addition to the existing formats:

```bash
readio export project.readio --format flac
readio export project.readio --format opus --bitrate 96k
```

`.ogg` remains Ogg/Vorbis; `.opus` selects Opus and is a separate format. Readio's generic Opus default is 96k; this is Readio's setting, not a claim of TTSForge default parity.

## Progress

`render`, `synth`, `preview`, `compose`, project render, and `spotify` report low-noise progress on stderr. It is enabled automatically on an interactive terminal. Use `--progress` to force it or `--no-progress` to suppress it:

```bash
readio compose manuscript.readio
readio compose manuscript.readio --progress
readio compose manuscript.readio --no-progress
readio compose manuscript.readio --json --progress
readio render --file episode.ssmd -o episode.mp3 --progress
readio spotify publish --file episode.ssmd --title "Episode" --json
```

Composition progress shows the current speech segment, active operation, completed and total segments, elapsed time, and an approximate ETA after enough segment work has completed. The ETA covers segment processing only. Assembly, complete-output loudness and true-peak processing, and composition artifact writing are shown as separate final stages.

`--json` keeps stdout to one machine-readable result object. Progress remains on stderr, automatic progress is disabled in JSON mode, and explicit `--progress` remains stderr-only. Verbose mode uses line-oriented stderr output instead of in-place terminal rewriting.

## Verbose diagnostics

Use the global repeatable verbosity option for live operational diagnostics:

```bash
readio -v speak "Hello"
readio -vv render episode.ssmd -o episode.mp3
readio speak "literal --verbose" --
```

`-v` shows timestamped lifecycle records at INFO level. `-vv` enables DEBUG-level Readio and selected engine details; additional repetitions are clamped to DEBUG. Verbose records always go to stderr, so ordinary output and `--json` results remain on stdout and stay machine-parseable. `--progress` is a separate user-facing progress control. When verbose mode and progress are combined, progress uses line-oriented stderr records instead of in-place terminal rewriting. Logs can contain paths and model or voice identifiers, so review them before sharing and never treat verbose mode as permission to expose document text, audio, or credentials.
When `-o` is supplied, `.wav`, `.flac`, `.mp3`, `.m4a`, `.ogg`, and `.opus` suffixes select the corresponding render encoder. `.ogg` means Ogg/Vorbis; `.opus` is distinct. Use `--format` when the output path is omitted or to select the automatic filename suffix. An explicit format and suffix must agree. Extensionless output paths receive the selected suffix, and unsupported suffixes fail before synthesis. Automatic names use the configured output directory and never overwrite an existing file. Explicit output remains atomic and requires `--force` for replacement.

M4A and Opus require an `ffmpeg` executable on `PATH`. WAV and FLAC use PCM16; MP3 and Ogg/Vorbis use the installed SoundFile/libsndfile codecs.

## SSMD consumption and authoring checks

For `.ssmd` and `.ssmd.md` inputs, Readio compiles SSMD through UtterPlan once, resolves document-local `voice_bindings` and missing invocation or configured roles, then lowers semantic segments to neutral engine requests. Document bindings remain authoritative, and unsupported explicit semantics fail before a synthesis session opens. Normal `speak`, `render`, and `spotify` commands do not rewrite source SSMD.

Readio accepts SSMD 0.9.x and UtterPlan >=0.3.4,<0.4/schema v3 only. SSMD 0.8 syntax, raw `<div>` directives, and legacy prosody aliases are rejected, not rewritten or migrated. Existing project plans using UtterPlan schema v1 or v2 are stale and must be rebuilt from valid SSMD 0.9 or plain text. This does not change Readio's own `readio.plan.v2` response schema.

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
producer-command | readio spotify publish --live --title "Live episode" --format mp3
readio spotify upload recording.m4a --title "Lecture 3" --show-id spotify:show:abc --wait 2m
readio spotify shows --json
readio spotify status spotify:episode:abc --wait
readio spotify doctor --json
```

Publish renders and uploads Readio source. Direct upload starts from caller-owned WAV, MP3, M4A, or OGG and never deletes or overwrites it. Without `--output`, generated publish media is temporary and deleted after success or failure; with `--output`, it is retained. `--chapters-from-markers` and caller-owned `--timeline FILE` are mutually exclusive, and either timeline path waits for READY before publishing. `--wait` optionally accepts a duration; `--wait-timeout` is deprecated. `--api-timeout` controls an upstream request separately from readiness waiting.

`spotify publish --live` reads plain-text stdin and leaves live rendering, engine capability checks, and temporary audio-file ownership to the application services. Without `--output`, generated audio is temporary and removed after success or failure; with `--output`, the file is retained.

Readio invokes `save-to-spotify --json`, reports its detected version in diagnostics, and does not inspect credentials, expose tokens, or perform authentication.

## Doctor

```bash
readio doctor
```

Doctor is offline/local by default and supports `readio doctor --json`. It reports Readio configuration, directories, TTS/SSMD dependencies, audio formats, and the upstream `save-to-spotify` path/version probe. It does not authenticate, inspect credentials, or perform Spotify network operations; use `readio spotify doctor` for the explicit external integration check.

## Agent Skill

The portable skill is in `skill/readio/SKILL.md`. It uses Readio templates and commands directly. It does not teach raw SSMD voice discovery, create, lint, temporary file management, or manual cleanup for normal podcast workflows.

## SSMD voice resolution and project role targets

Document-local SSMD bindings retain the provider-qualified `voice_bindings` syntax. They keep a portable document's speaker choices with the source:

```yaml
voice_bindings:
  kokoro:
    moderator: af_sarah
    architect: am_michael
```

SSMD syntax is unchanged. A role bound in more than one provider namespace in the same document is ambiguous; Readio reports an error rather than selecting one. Resolution precedence is document binding, invocation `--voice-bind`, project role target, global configured role, then a direct semantic reference or context-resolved native voice ID. Document bindings remain authoritative.

Discover runnable voices with `readio voices list --json` or filter with `readio voices list --lang de`. Semantic references replace numbered voice selectors. For example, bind Kokoro with `kokoro:v1.0/af_heart` and Piper with `piper:en_US-amy-medium`. Persist global roles with `readio roles bind ROLE REF`; use `--engine ENGINE` for a native voice ID when its engine cannot be inferred, and prefer a semantic reference when its target must be explicit. `readio roles list` reports engine-qualified targets. v0.3 `[voices.<provider>.roles]` settings are converted only by the explicit `readio config migrate` command; normal runtime code does not read them.

For deterministic one-run automation, use repeatable `--voice-bind ROLE=REF` options. Readio resolves semantic references to engine-qualified targets; document bindings continue to take precedence:

```bash
readio render --file episode.ssmd \
  --voice-bind moderator=kokoro:v1.0/af_sarah \
  --voice-bind architect=kokoro:v1.0/am_michael
```

`--resolve-voices` prompts only when explicitly requested from an interactive TTY. It never persists choices. JSON, agents, scripts, and non-TTY execution must use `--voice-bind`. `readio ssmd bind FILE --voice-bind ROLE=VOICE_ID -o OUTPUT.ssmd` explicitly materializes bindings into a new source file; ordinary consumption never edits SSMD.

Project-local role targets let one SSMD document use multiple synthesis engines without changing the semantic SSMD speaker references:

```bash
cd episode.readio
readio plan bind host kokoro:v1.0/af_sarah
readio plan bind guest piper:en_US-amy-medium
readio plan roles
readio plan
readio synth
```

New bindings are stored role-centrically in `project.json` under `settings.ssmd.role_bindings`, with each target's `engine`, canonical `voice`, and optional `target_id`. Selector provenance is not persisted. Binding a semantic reference retains its structured engine and target identity and does not set a project-wide provider. Use `readio plan bind ROLE REF PROJECT` or `--project PROJECT` from outside the project; nested working directories are discovered. For native voice IDs, pass `--engine` when needed and use a semantic reference if the target is ambiguous.

v0.3 manifests with `settings.ssmd.voice_bindings` require explicit `readio project migrate PROJECT`; normal project reads do not fall back to provider-keyed data. After migration, `settings.ssmd.role_bindings` supports multiple engines without a project-wide provider. Use `readio plan roles --engine ENGINE` to filter inspection.

`readio plan bind` does not rewrite SSMD or change the semantic plan ID. It changes acoustic synthesis settings, so `readio status` leaves planning current and reports synthesis stale; run `readio synth` to refresh it. Synthesis routes bound segments through each target's engine and uses the normal project synthesis selection for unbound segments. Engines and sessions are resolved per route, so a mixed-engine project does not require one project-wide provider.

## Persistent incremental projects

For resumable builds, create a project and use explicit stages:

```bash
readio project init manuscript.md -o manuscript.readio
cd manuscript.readio
readio plan                         # semantic only; no TTS
readio preview manuscript.readio --select first:3 --voice de-ko-01 -o preview.wav
readio synth manuscript.readio --voice de-ko-01
readio compose manuscript.readio --target-lufs -18
readio export manuscript.readio --format mp3
readio status manuscript.readio --json
readio render manuscript.readio --format mp3  # build stale stages
```

Chapter-aware audiobook projects accept EPUB files and `.ssmdbook` directories or `.ssmdbook.zip` bundles produced by ssmdconvert. EPUB and ZIP inputs are materialized as standalone editable `.ssmdbook` directories. Readio attaches disposable project state under `.readio/` and indexes the canonical chapter files in place instead of copying them.

```bash
readio audiobook chapters novel.epub
readio audiobook init novel.epub --chapters 2-20
readio audiobook chapters novel.ssmdbook.zip
readio audiobook init novel.ssmdbook --chapters 3-4,7
cd novel.ssmdbook
readio plan roles
readio plan
readio synth --voice en-ko-01
readio compose
readio export --format m4a
readio audiobook export . --format m4b --cover cover.jpg
# Or build all stale stages with:
readio render novel.ssmdbook --format m4a
```

Chapter selectors use the available 1-based source chapter numbers reported by `readio audiobook chapters` and preserve source order, including non-contiguous bundle subsets. Edit canonical chapter files under `chapters/` (or paths listed in `manifest.json`). Readio reads edits immediately, refreshes only its local index, and replans/resynthesizes affected content. After chapter edits, run `ssmdconvert book refresh novel.ssmdbook` to update canonical manifest hashes; Readio never does this automatically. Deleting `.readio/` removes local plans, caches, and outputs but leaves the editable book intact; `readio audiobook init novel.ssmdbook` can recreate that state. Generic EPUB rendering remains one combined document, so use audiobook commands for chapter-aware processing.

Audiobook projects can be exported as M4B with embedded chapters: `readio audiobook export . --format m4b`. Title and author default from the book's metadata and can be overridden with `--title` and `--author`. Readio's AAC bitrate default is 192k; use `--bitrate` to choose another supported bitrate.

Cover art is explicit-only: pass `--cover cover.jpg` or a PNG path. Readio validates, hashes, attaches, and tracks the image. Automatic source cover extraction is not part of the current ssmdconvert integration; provide a JPEG/PNG cover explicitly. M4B is audiobook-only; generic `readio export` supports WAV, FLAC, MP3, M4A, Ogg/Vorbis, and Opus, never M4B.

See `docs/projects.md` and `docs/incremental-rendering.md` for the project layout, cache identities, status diagnostics, and invalidation matrix.
Project synthesis, composition, and export defaults can be saved before execution with `readio project settings set PROJECT ...`; inspect with `readio project settings show PROJECT --json`. Requestless project operations use saved settings, while explicit invocation overrides remain transient. Audiobook M4B output, metadata, cover, and bitrate can also be saved and are checked by `readio status`. See [project settings](docs/projects.md#desired-pipeline-settings), [CLI reference](docs/cli.md#saved-project-pipeline-settings), and [Python API](docs/api.md#projects-and-audiobooks).
