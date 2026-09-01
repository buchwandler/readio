# Troubleshooting

```bash
readio doctor --json
readio ssmd check FILE --json
readio config validate
```

## Model discovery and defaults

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
