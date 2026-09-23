# Troubleshooting

```bash
readio doctor --json
readio ssmd check FILE --json
readio config validate
```

## Verbose diagnostics

When a command needs runtime-level investigation, repeat the global verbosity flag:

```bash
readio -v speak "test"
readio -vv doctor
readio -vv render input.ssmd -o out.wav
```

Use `-v` for timestamped INFO lifecycle events and `-vv` for DEBUG details from Readio and PyKokoro. Logs are written to stderr, while normal output and JSON stay on stdout. `--progress` is independent; verbose mode changes TTY progress to line-oriented output so it does not overwrite log records. Logs may contain paths, model IDs, and voice IDs. Review them before sharing. Complete document text, raw audio, Spotify credentials, and authorization responses are not logged.

### Readio speaks the filename instead of the file

Older versions require `--file`:

```bash
readio render --file episode.ssmd -o episode.mp3
```

Upgrade to a version with positional file detection, or keep using the explicit form for scripts. A current Readio invocation such as `readio render episode.ssmd -o episode.mp3` loads the SSMD body; use `--input-format text` only when the filename itself should be spoken.

## Model discovery and defaults

- **PyKokoro import/API mismatch:** if the error reports `pykokoro.import_failed`, `pykokoro.version_unsupported`, or `pykokoro.discovery_api_missing`, install PyKokoro >=0.9.2,<0.10 and verify `python -c "from pykokoro import discover_models; from pykokoro.tokenizer import TokenizerConfig"`. Run `readio doctor --json`. Do not import private PyKokoro modules.

### PyKokoro model discovery is unavailable

For `cannot import name 'discover_models' from 'pykokoro'`, first distinguish a stale checkout/API mismatch from a registry outage:

```bash
python -c "import pykokoro; print(pykokoro.__file__); print(pykokoro.__version__)"
python -c "from pykokoro import discover_models; print(discover_models)"
python -m pip show pykokoro
readio doctor --json
```

`readio doctor --json` reports the imported module path, distribution metadata version, module version, and each public API symbol. Do not work around this by importing private registry modules.

- **Offline registry failure:** `pykokoro.registry_unavailable` means registry metadata or its cache is unavailable. Use an online `readio models list` once to populate the cache.
- **Offline runtime asset failure:** registry metadata can be available while synthesis fails because model or voice assets are not cached. Install or cache the selected assets; this is distinct from registry discovery failure.

## Plan diagnostics

`readio render --dry-run` reports one-shot problems before any TTS model is loaded; a normal render that fails planning prints the same plan and exits 1:

- **`backend_resolution_failed`**: PyKokoro could not concretize an automatic model selection. Check `readio doctor --json`, then select explicitly with `--model`/`--model-source`.
- **`model_language_incompatible`**: the selected model does not declare the requested language (for example a German-only model with `--lang en-us`). Pick a model from `readio models list --language LANG --json` or set `--lang` to a language the model declares.
- **`model_runtime_unavailable`**: the model exists in the registry but is not runnable in the installed runtime. Choose a model with `runtime_available: true` and a `ready` status.
- **`synthesis_incomplete`**: the plan could not become concrete (missing model/source/quality/voice). Provide `--model` explicitly and retry.

- **Unexpected pronunciation routing:** inspect the plan's `language_detection` and `detect_languages` fields. SSMD `language_detection` hints and CLI detection options route pronunciation fragments while retaining the selected acoustic model language.
- **`ssmd_unresolved_voice` / `ssmd_voice_unavailable`**: an SSMD voice reference has no binding or its binding is outside the active model roster. Resolve one invocation with repeatable `--voice-bind ROLE=VOICE_ID`, a project with `readio plan bind ROLE VOICE`, or a user-global fallback with `readio roles bind ROLE VOICE_ID`; inspect stable selectors with `readio voices list --model MODEL --json`. Never use `--resolve-voices` in agents, scripts, or JSON mode.
- **`synthesis.stale.project_voice_bindings_changed`**: the project's effective voice settings differ from those recorded by active synthesis. The semantic plan remains current and cached audio is retained; run `readio synth` to refresh the acoustic output.
- **`encoder_unavailable` / `output_format_conflict`**: the requested audio format needs an unavailable backend (M4A requires `ffmpeg` on `PATH`) or `--format` disagrees with the output suffix. Choose WAV/MP3/OGG, install FFmpeg, or align format and suffix.

- **Model registry unavailable:** run `readio models list --offline --json` to use the cache. Without a valid cache, run the online command once; discovery never downloads model weights.
- **Unknown model:** run `readio models list --json`; do not maintain a hardcoded model/voice inventory in an agent workflow.
- **Incompatible voice or lexicon:** inspect `readio models show MODEL --json` and select values from the active model capability metadata. Use the named selector `crane`, not the language-qualified asset ID `de-de:crane`; `de-crane` is an acoustic model. A `lexicons` value of `null` means capability enumeration is unknown, not that no lexicons exist.
- **Default validation failure:** `readio defaults set LANG ...` validates the complete model/language/quality/voice/lexicon combination before saving. Use `--allow-experimental` only when intentionally opting into an experimental frontend.
- **Default validation failure:** `readio defaults set LANG ...` validates the complete model/language/quality/voice/lexicon combination before saving. Use `--no-lexicons` for explicit provider-only pronunciation and `--auto-lexicons` to remove an inherited lexicon override. `--allow-experimental` is only for intentionally opting into an experimental frontend.
- **Tokenizer policy failure:** `--g2p-fallback` must be `none`, `espeak`, or `goruut`; `--lexicon-data-policy` must be `auto` or `installed-only`. The latter controls lexicon acquisition and is distinct from model-registry `--offline`.

### Explicit spaCy model unavailable

If runtime reports an unavailable model such as `de_core_news_lg`, use `--spacy auto` to select an installed compatible tier, install the requested spaCy model, choose another explicit tier, or use `--spacy off`.

### Slow short-sentence synthesis

If verbose logs repeat `Short sentence phrase cut ... trying another phrase`, use `--short-sentence wrap` for lower latency or `--short-sentence off` to bypass the workaround. `phrase` and `randomized-phrase` intentionally retain carrier-phrase extraction and may require additional inference calls.

## Render manifest failures

A successful bounded render with `--manifest` writes `<audio>.readio.json` beside the committed audio. Compare the manifest's embedded `readio.plan.v2` and environment before comparing audio bytes when two renders differ:

```bash
readio render --file episode.ssmd -o episode.mp3 --manifest --json
cat episode.mp3.readio.json
```

`--manifest` is not supported with `--live`, `speak`, `plan`, dry runs, or publishing. A sidecar failure does not delete valid audio. The command returns `render.manifest_error`; JSON errors include both `audio_path` and `manifest_path`.

- **Missing `save-to-spotify`:** install/configure the external CLI and ensure it is on `PATH`; Readio does not install or authenticate it.
- **Authentication failure:** configure the upstream integration directly with `save-to-spotify setup`. Readio never inspects its token files.
- **Missing FFmpeg:** M4A rendering requires `ffmpeg` on `PATH`; choose WAV/MP3/OGG or install/configure FFmpeg.
- **Unsupported codec:** inspect `readio doctor --json` and choose an available audio format.
- **SSMD preflight failure:** run `readio ssmd check FILE --json`, then correct the source or provide explicit voice bindings. Do not guess syntax or silently collapse roles.
- **Output conflict:** choose another path or pass `--force` only when replacing the existing artifact is intentional.
- **Timeline failure:** ensure the JSON file is an object, use only one timeline mode, and understand that the episode must be READY before publication.
