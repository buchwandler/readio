# Troubleshooting

Start with Readio's diagnostic command:

```bash
readio doctor --json
```

Doctor reports specialist package versions and VoiceRender engine installation, status, version, and compatibility. Install a missing synthesis engine through VoiceRender, for example:

```bash
python -m pip install "voicerender[kokoro]>=0.2.0,<0.3"
```

## A Readio command is unavailable

Confirm Readio 0.5 is installed in the active Python environment and inspect `readio --help`. The current surface includes delegated engines, models, voices and profiles; `config`; `speak`; single-document `render`; `audiobook convert` and `audiobook build`; and `doctor`. Former generic project, plan, render-manifest, backend-adapter, and Spotify commands have no compatibility aliases.

## Speech playback fails

`readio speak` plays through the current device by default. Check that the selected VoiceRender engine is installed and the host has a working audio output device. Use `--no-play --output FILE.wav` to synthesize without playback. Use `readio doctor --json` for engine diagnostics and consult VoiceRender for backend-specific options or errors.

## Document or audiobook stage fails

Readio delegates conversion, planning, synthesis, composition, and export to their specialist packages. Check the relevant specialist CLI and preserve the exact variant returned by UtterPlan when resuming a manual audiobook workflow. Readio returns specialist results and propagates specialist exceptions.

Use `readio render ... --stop-after STAGE --json` to inspect single-document stages (`convert`, `plan`, `render-plan`, `synth`, `compose`, `export`), or `readio audiobook build ... --stop-after STAGE --json` for audiobook stages (`compile`, `render-plan`, `synth`, `compose`, `export`). Progress is written to stderr, separately from JSON stdout. Retain document intermediates with `--artifacts DIR` or `--keep-artifacts`.

## Settings are not applied

Readio does not auto-discover settings. Pass `--config PATH` to select a file; check it with `readio config validate PATH`. Precedence is specialist defaults < settings file < explicit CLI options.

## Legacy project state

Version 0.5 is a clean break. Readio does not read or migrate `.readio` state, old project directories, manifests, or stage records. Start from source material and create a new SSMDConvert `.ssmdbook` workspace, or use the specialist packages directly.
