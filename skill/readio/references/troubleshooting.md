# Troubleshooting

Start with Readio's retained diagnostic command:

```bash
readio doctor --json
```

Doctor reports specialist package versions and VoiceRender engine installation, status, version, and compatibility. Install a missing synthesis engine through VoiceRender, for example:

```bash
python -m pip install "voicerender[kokoro]>=0.2.0,<0.3"
```

## Readio command is unavailable

Confirm that Readio 0.5 is installed in the active Python environment and inspect `readio --help`. The retained commands are `speak`, `audiobook convert`, `audiobook build`, and `doctor`. The former `render`, `synth`, `plan`, `project`, `voices`, `models`, `defaults`, `config`, and `spotify` command families have been removed.

## Speech playback fails

`readio speak` plays audio through the current device. Check that the selected VoiceRender engine is installed and that the host has a working audio output device. Use `readio doctor --json` for engine diagnostics. For backend-specific options or errors, consult VoiceRender directly.

## Audiobook stage fails

Readio delegates conversion, compilation, synthesis, composition, and export to their specialist packages. Check the relevant specialist CLI and preserve the exact variant returned by UtterPlan when resuming a manual workflow. Readio returns specialist results and propagates specialist exceptions; it does not translate them into a parallel Readio error model.

Use `readio audiobook build ... --stop-after STAGE --json` to inspect results through a selected stage. The accepted stage names are `compile`, `render-plan`, `synth`, `compose`, and `export`.

## Legacy project state

Version 0.5.0 is a clean break. Readio does not read or migrate `.readio` state, old project directories, manifests, or stage records. Start from source material and create a new SSMDConvert `.ssmdbook` workspace, or use the specialist packages directly.
