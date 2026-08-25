# Troubleshooting

```bash
readio doctor --json
readio ssmd check FILE --json
readio config validate
```

- **Missing `save-to-spotify`:** install/configure the external CLI and ensure it is on `PATH`; Readio does not install or authenticate it.
- **Authentication failure:** configure the upstream integration directly with `save-to-spotify setup`. Readio never inspects its token files.
- **Missing FFmpeg:** M4A rendering requires `ffmpeg` on `PATH`; choose WAV/MP3/OGG or install/configure FFmpeg.
- **Unsupported codec:** inspect `readio doctor --json` and choose an available audio format.
- **SSMD preflight failure:** run `readio ssmd check FILE --json`, then correct the source or provide explicit voice bindings. Do not guess syntax or silently collapse roles.
- **Output conflict:** choose another path or pass `--force` only when replacing the existing artifact is intentional.
- **Timeline failure:** ensure the JSON file is an object, use only one timeline mode, and understand that the episode must be READY before publication.
