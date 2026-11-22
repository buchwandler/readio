# Troubleshooting

Start with Readio's diagnostic command:

```bash
readio doctor --json
```

Doctor reports specialist package versions and VoiceRender engine installation/status. Install a missing synthesis engine through VoiceRender, for example:

```bash
python -m pip install "voicerender[kokoro]>=0.2.5,<0.3"
```

## A Readio command is unavailable

Confirm Readio is installed in the active Python environment and inspect `readio --help`. Readio exposes unified `render`, `status`, `speak`, and management commands; direct stage controls belong to the specialist CLIs.

## Speech playback fails

`readio speak` plays through the current device by default. Check that the selected VoiceRender engine is installed and that the host has a working audio output device. Use `--no-play --output FILE.wav` to synthesize without playback. Use `readio doctor --json` for engine diagnostics and consult VoiceRender for backend-specific options or errors.

## A render workflow fails

Readio delegates conversion, planning, synthesis, composition, and export to specialist packages. Start with `readio render --dry-run` or `readio status` to inspect specialist-backed workflow state. Use `-v`/`-vv` for diagnostics, then consult the relevant specialist CLI for package-specific details. Preserve UtterPlan's exact projected variant when resuming a manual specialist sequence. Progress is written to stderr, separately from JSON stdout.

## A bundle cannot be opened

The pipeline requires an editable `.ssmd-bundle` directory that SSMDConvert can validate. Inspect the bundle with SSMDConvert and use its diagnostics to resolve manifest or content problems.
