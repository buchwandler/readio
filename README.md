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

### Optional spaCy linguistic planning

Install the optional Utterplan spaCy support when local grammatical annotations are desired:

```bash
python -m pip install "readio[spacy]"
```

Install a compatible local spaCy language model separately. Readio never downloads models implicitly. The default `reader.spacy = "auto"` tries the best locally installed model and falls back to Utterplan's analyzer when none is available. The explicit `sm`, `md`, `lg`, and `trf` settings require the selected local model tier and fail if it is unavailable. `off` disables spaCy analysis.

`readio plan` stores token annotations and linguistic provenance in the Utterplan v2 artifact. Rendering an existing project plan consumes those stored annotations and does not rerun spaCy when the engine, voice, or acoustic settings change. Direct one-shot commands such as `readio speak` may use the selected backend's local frontend because they do not consume a persisted semantic plan.

## Python API

Use `readio.api` from Python applications for typed synchronous speech, project, catalog, configuration, and diagnostics services. Start with the [Python API guide](docs/api.md) and executable [planning example](examples/python_api.py).

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

A single existing positional token is also treated as a file path by `speak`, `render`, and `spotify publish`, including `.ssmd` and Markdown files:

```bash
readio speak README.md
readio render episode.ssmd -o episode.mp3
readio spotify publish episode.ssmd --title "Episode"
```

For scripts, prefer the explicit `--file PATH` form. A missing path-like token fails instead of being spoken as a filename. Use `--input-format text` to force an existing filename to remain literal text.

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
readio config set reader.pause_mode auto
readio config set ssmd.voice_provider kokoro
```

The default configuration uses `platformdirs` for the config, template, ingest, and output locations. `READIO_CONFIG` overrides the config file path. Existing legacy files containing only `[reader]` continue to load and are upgraded to schema 2 when saved.

The configuration contains reader settings, SSMD defaults, provider-specific voice IDs, and logical role bindings. Templates refer to roles such as `host`, `analyst`, `guest`, and `narrator`, while ordinary literal text continues to use `reader.voice`.

### Model discovery and language defaults

PyKokoro >=0.9.11,<0.10 is the runtime contract and owns the model, language, voice, quality, frontend, and named-lexicon catalog. Readio also requires OnnxVoice >=0.1.6,<0.2 for the runtime model and timing integration. Discovery is metadata-only and does not download model weights:
Readio v0.2.5 is tested against PyKokoro 0.9.11.

Readio selects synthesis through an explicit engine registry. PyKokoro and Piper are the supported engines; engine identity is recorded separately from distribution provider metadata so future adapters can be added without changing selectors or configuration.

For `readio voices list`, a registered engine/system name passed through `--model` is a shortcut when `--engine` is omitted: `piper` and `pipersynth` select Piper, while `pykokoro` and `kokoro` select PyKokoro. Concrete model IDs and Piper voice targets remain `--model` filters; when `--engine` is present, `--model` is always treated as a concrete filter.


The canonical engine IDs are `pykokoro` and `piper`. The alias `pipersynth` is accepted as a compatibility alias for `piper`.

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
readio render --engine piper --voice de_DE-thorsten-medium --lang de --dry-run --json --text "Hallo Welt"
readio speak --engine piper --voice de_DE-thorsten-medium --lang de "Hallo Welt"
readio render --engine piper --voice de_DE-thorsten-medium --lang de --manifest -o article.wav --text "Hallo Welt"
readio render --engine pipersynth --voice de_DE-thorsten-medium --lang de --dry-run --json --text "Hallo Welt"
```

Use `--speaker NAME_OR_ID` for a multi-speaker Piper bundle. Piper live mode is not supported yet; use bounded input.

Use `--refresh` to refresh registry metadata only. `--offline --refresh` is invalid. Offline metadata requires a cached registry; offline synthesis additionally requires cached model and voice assets.

Persist a validated default per language. Language keys are normalized, and locale-specific profiles fall back to their base language:

```bash
readio defaults set de --model de-thorsten --lexicon crane --offline
readio defaults show de --json
readio defaults show de-at --json
readio render --lang de --file notes.md
```

When a model is selected, Readio fills its normalized source, default voice, and preferred quality, then validates language compatibility, voice roster, quality, named lexicons, and experimental frontend permission before saving. `--no-lexicons` selects explicit provider-only pronunciation (`lexicons=[]`); `--auto-lexicons` returns to engine language defaults (`lexicons=null`). Repeat `--lexicon` to preserve ordered layered lookup.
Readio defaults `pause_mode` to `auto`, enabling PyKokoro's automatic pause analysis. Use `--pause-mode tts` to leave pause timing to the acoustic model or `--pause-mode manual` for explicit boundary pauses. A persisted `reader.pause_mode` remains the default for that installation.
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

No Readio or Python installation is needed on the authoring system: choose one guide and attach it with the task and source material. When the harness supports artifacts, ask it to create and return one downloadable `.ssmd` file; otherwise save the raw SSMD response as a file. Rendering and final validation happen later on a system where Readio is installed.

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
    "schema_version": 2,
    "plan_id": "...",
    "sha256": "...",
    "path": "plan/document.utterplan.json"
  },
  "render": {
    "engine": "piper",
    "target": { "id": "de_DE-thorsten-medium", "language": "de", "voice": "thorsten" },
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

A successful render writes the audio and a colocated `<audio>.readio.json` sidecar. The sidecar uses schema `readio.render-manifest.v1` and records the exact executed `readio.plan.v2`, its canonical SHA-256, the semantic plan identity, the final encoded audio hash and byte count, render summary facts, document metadata, and final marker offsets. The plan is pre-execution intent; the manifest is post-execution evidence.

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

`-v` shows timestamped lifecycle records at INFO level. `-vv` enables DEBUG-level Readio and PyKokoro details; additional repetitions are clamped to DEBUG. Verbose records always go to stderr, so ordinary output and `--json` results remain on stdout and stay machine-parseable. `--progress` is a separate user-facing progress control. When verbose mode and progress are combined, progress uses line-oriented stderr records instead of in-place terminal rewriting. Logs can contain paths and model or voice identifiers, so review them before sharing and never treat verbose mode as permission to expose document text, audio, or credentials.
When `-o` is supplied, its `.wav`, `.mp3`, `.m4a`, or `.ogg` suffix selects the encoder. Use `--format` when the output path is omitted or to select the automatic filename suffix. An explicit format and suffix must agree. Extensionless output paths receive the selected suffix, and unsupported suffixes fail before synthesis. Automatic names use the configured output directory and never overwrite an existing file. Explicit output remains atomic and requires `--force` for replacement.

M4A output requires an `ffmpeg` executable on `PATH`. WAV uses PCM16, while MP3 and OGG use the installed SoundFile/libsndfile codecs.

## SSMD consumption and authoring checks

For `.ssmd` inputs, Readio parses the document through the supported SSMD 0.8.x API and passes a PyKokoro 0.9 `SSMDRenderConfig` containing only missing Readio role defaults. Document `voice_bindings` remain authoritative, invocation `--voice-bind` values override configured provider roles, and concrete targets must belong to the active model roster. Normal `speak`, `render`, and `spotify` commands do not invoke `ssmd create`, rewrite the source, or require generic round-trip validation.

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

Discover runnable registry voices and stable selectors with `readio voices list --json` or `readio voices list --lang de`. For Kokoro, a real en-US identity is `en_us-ko-4` -> `af_heart` (Kokoro v1.0); `--lang en-us` filters the inventory, while `en_us-ko-4` is the normalized stable selector. Inspect it with `readio voices show en_us-ko-4 --json`. Kokoro selectors use `<lang>-ko-<slot>` and Piper selectors use `<lang>-pi-<slot>`; these identities come from the authoritative voice registry, while canonical engine voice IDs remain visible and accepted. Persist reusable SSMD roles with `readio roles bind ROLE VOICE_ID` and inspect them with `readio roles list`. Selectors are stable short lookup aliases; persisted configuration and SSMD continue to use canonical concrete voice IDs. For deterministic one-run automation, use repeatable options:
Runtime voice inventories are model-specific. Use `readio voices list --model MODEL --json` for concrete IDs; a configured portable role such as `host` must be bound to a voice supported by the selected model. Readio reports the active model and valid voices before inference when a binding is incompatible.

```bash
readio render --file episode.ssmd \
  --voice-bind moderator=af_sarah \
  --voice-bind architect=am_michael
```

`--resolve-voices` prompts only when explicitly requested from an interactive TTY. It never persists choices. JSON, agents, scripts, and non-TTY execution must use `--voice-bind` instead. Document bindings remain authoritative, and unresolved roles are reported before TTS or external publishing work begins. `readio ssmd bind FILE --voice-bind ROLE=VOICE_ID -o OUTPUT.ssmd` explicitly materializes bindings into a new source file; ordinary consumption never edits SSMD.


Project-local role choices belong to the project rather than portable SSMD or user-global config:

```bash
cd episode.readio
readio plan roles
readio plan bind narrator en_us-ko-4
readio plan unbind narrator
readio plan
```

Resolution precedence is document binding, invocation `--voice-bind`, project binding, global configured role, then direct concrete voice. `readio plan bind` does not rewrite SSMD or change the semantic plan ID. It changes acoustic synthesis settings, so `readio status` leaves planning current and reports synthesis stale; run `readio synth` to refresh it.

A project may persist `settings.ssmd.voice_provider` as its active SSMD provider. If it is absent, Readio infers the provider when there is exactly one non-empty provider binding namespace. Projects with neither an active provider nor project binding namespaces retain the global configuration fallback; multiple namespaces without an active provider are reported as ambiguous. `readio plan roles`, `bind`, `unbind`, and project synthesis use this same effective provider. Binding a stable selector such as `en-pi-13` stores its canonical Piper voice and activates Piper for that project.

Project synthesis defaults its engine from the active provider rather than inheriting global `reader.engine` or `reader.voice`. An explicit `readio synth --engine ...` is a run-local override and does not change project settings. For example:

```bash
readio plan bind narrator en-pi-13
readio plan roles
readio synth
readio synth --engine pykokoro  # this run only
```

PyKokoro uses runtime voice bindings and can switch voices within its loaded pipeline. Piper is target-bound: Readio resolves each role to a Piper voice bundle, validates all targets before opening a model, then routes speech segments through one reusable session per distinct target. Multi-target progress shows the role-to-voice map and reports each model load separately. The semantic plan stays symbolic and unchanged by role bindings.

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

For EPUB audiobooks, Readio discovers selectable chapters and persists the chosen chapter Markdown as editable project inputs:

```bash
readio audiobook chapters novel.epub
readio audiobook init novel.epub --chapters 2-20
cd novel.readio
readio plan roles
readio plan
readio synth --voice en-ko-01
readio compose
readio export --format m4a
# Or build all stale stages with:
readio render novel.readio --format m4a
```

Chapter numbers are the flat, 1-based order reported by `readio audiobook chapters`. Selection is saved during initialization, so later project commands do not need `--chapters`. Edit files under `document/chapters/` to change semantic inputs. Readio replans and resynthesizes only affected content. The copied EPUB is provenance; if it changes, status reports a stale source and the project must be reinitialized rather than silently reimported. EPUB is not a direct render input or a separate build pipeline.

This first EPUB workflow preserves chapter boundaries in the project timeline, but does not implement M4B encoding or embedded container chapter metadata.

See `docs/projects.md` and `docs/incremental-rendering.md` for the project layout, cache identities, status diagnostics, and invalidation matrix.
