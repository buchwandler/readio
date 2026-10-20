# Readio documentation

Readio orchestrates native SSMDConvert `.ssmdoc` and `.ssmdbook` workspaces through specialist speech packages. Specialist APIs own the stage schemas, persisted artifacts, and detailed diagnostics; Readio provides workflow sequencing, a CLI, and direct literal-text speech.

```{toctree}
:maxdepth: 1

architecture
cli
api
changelog
```

## Specialist workflow

The package-owned stages are:

```text
SSMDConvert → UtterPlan / RenderPlan → VoiceRender → AudioCompose → AudioExport
```

Start with the [architecture guide](architecture.md), use the [CLI reference](cli.md) for Readio commands, and see the [Python API guide](api.md) for service calls. Consult each specialist package for complete package-specific options.

## Installation

Readio requires Python 3.10 or newer. Install it with `python -m pip install readio`, then install a speech engine through VoiceRender, for example `python -m pip install "readio[kokoro]"`. The optional `readio[documents]`, `readio[spacy]`, and `readio[audioexport]` extras enable document formats, UtterPlan spaCy support, and AudioExport respectively.

## Readio commands

```bash
readio init story.txt -o story.ssmdoc
readio run story.ssmdoc --profile narrator.renderprofile.toml --language en-US
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello."
readio doctor --json
```

The pipeline commands operate on `.ssmdoc` and `.ssmdbook` bundles. Readio writes no parallel project state; stage outputs remain owned by the specialist packages.

## No legacy migration

Version 0.5 deliberately removes Readio-owned projects, settings, plans, manifests, and stage records. There is no migration command or compatibility layer. Recreate work from source material with `readio init`, or continue with the specialist packages directly.
