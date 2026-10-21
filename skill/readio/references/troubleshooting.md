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

Confirm Readio is installed in the active Python environment and inspect `readio --help` or `readio bundle --help`. SSMD lifecycle commands are grouped under `bundle`; management commands, `speak`, and `doctor` remain at the root.

## Speech playback fails

`readio speak` plays through the current device by default. Check that the selected VoiceRender engine is installed and that the host has a working audio output device. Use `--no-play --output FILE.wav` to synthesize without playback. Use `readio doctor --json` for engine diagnostics and consult VoiceRender for backend-specific options or errors.

## A bundle stage fails

Readio delegates conversion, planning, synthesis, composition, and export to specialist packages. Check the relevant specialist CLI and preserve UtterPlan's exact projected variant when resuming a manual stage sequence. Use `readio bundle run ... --json` or a stage-specific command to identify the failing package. Progress is written to stderr, separately from JSON stdout.

## A bundle cannot be opened

The pipeline requires an editable `.ssmd-bundle` directory that SSMDConvert can validate. Inspect the bundle with SSMDConvert and use its diagnostics to resolve manifest or content problems.
