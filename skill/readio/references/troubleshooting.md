# Troubleshooting

```bash
readio doctor --json
readio ssmd check FILE --json
readio config validate
```

### Readio speaks the filename instead of the file

Older versions require `--file`:

```bash
readio render --file episode.ssmd -o episode.mp3
```

Upgrade to a version with positional file detection, or keep using the explicit form for scripts. A current Readio invocation such as `readio render episode.ssmd -o episode.mp3` loads the SSMD body; use `--input-format text` only when the filename itself should be spoken.

## Model discovery and defaults

- **PyKokoro import/API mismatch:** if the error reports `pykokoro.import_failed`, `pykokoro.version_unsupported`, or `pykokoro.discovery_api_missing`, install the required PyKokoro 0.9.x release and verify `python -c "from pykokoro import discover_models"`. Run `readio doctor --json`. Do not import private PyKokoro modules.

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

- **Model registry unavailable:** run `readio models list --offline --json` to use the cache. Without a valid cache, run the online command once; discovery never downloads model weights.
- **Unknown model:** run `readio models list --json`; do not maintain a hardcoded model/voice inventory in an agent workflow.
- **Incompatible voice or lexicon:** inspect `readio models show MODEL --json` and select values from the active model capability metadata. A `lexicons` value of `null` means capability enumeration is unknown, not that no lexicons exist.
- **Default validation failure:** `readio defaults set LANG ...` validates the complete model/language/quality/voice/lexicon combination before saving. Use `--allow-experimental` only when intentionally opting into an experimental frontend.
- **Locale fallback surprise:** `defaults show de-at --json` reports whether the exact `de-at` profile or base `de` profile matched. `--no-lexicons` explicitly clears inherited lexicons.

- **Missing `save-to-spotify`:** install/configure the external CLI and ensure it is on `PATH`; Readio does not install or authenticate it.
- **Authentication failure:** configure the upstream integration directly with `save-to-spotify setup`. Readio never inspects its token files.
- **Missing FFmpeg:** M4A rendering requires `ffmpeg` on `PATH`; choose WAV/MP3/OGG or install/configure FFmpeg.
- **Unsupported codec:** inspect `readio doctor --json` and choose an available audio format.
- **SSMD preflight failure:** run `readio ssmd check FILE --json`, then correct the source or provide explicit voice bindings. Do not guess syntax or silently collapse roles.
- **Output conflict:** choose another path or pass `--force` only when replacing the existing artifact is intentional.
- **Timeline failure:** ensure the JSON file is an object, use only one timeline mode, and understand that the episode must be READY before publication.
