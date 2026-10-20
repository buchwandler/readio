# Troubleshooting

Start with Readio's diagnostic command:

```bash
readio doctor --json
```

Doctor reports specialist package versions and VoiceRender engine installation/status. Install a missing synthesis engine through VoiceRender, for example:

```bash
python -m pip install "voicerender[kokoro]>=0.2.3,<0.3"
```

## A Readio command is unavailable

Confirm Readio 0.5 is installed in the active Python environment and inspect `readio --help`. Current commands include native workspace operations (`init`, `plan`, `render-plan`, `render`, `compose`, `export`, `run`), delegated management (`engines`, `models`, `voices`, `voice`, `profile`), `speak`, and `doctor`. There is no `config` or `audiobook` command and no compatibility aliases for the former project or pipeline commands.

## Speech playback fails

`readio speak` plays through the current device by default. Check that the selected VoiceRender engine is installed and the host has a working audio output device. Use `--no-play --output FILE.wav` to synthesize without playback. Use `readio doctor --json` for engine diagnostics and consult VoiceRender for backend-specific options or errors.

## A document or book stage fails

Readio delegates conversion, planning, synthesis, composition, and export to specialist packages. Check the relevant specialist CLI and preserve UtterPlan's exact projected variant when resuming a manual stage sequence. Use `readio run ... --json` or stage-specific commands to identify the failing package. Progress is written to stderr, separately from JSON stdout.

## A workspace cannot be opened

Workspace paths must end in `.ssmdoc` or `.ssmdbook` and be valid SSMDConvert bundles. Readio does not load former Readio project directories or attach parallel state.

## Legacy project state

Version 0.5 is a clean break. Readio does not read or migrate old project directories, settings, manifests, or stage records. Start from source material and create a native SSMDConvert workspace with `readio init`, or use the specialist packages directly.
