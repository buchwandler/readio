# Readio documentation

Readio's workflow gateway uses a visible, user-owned `readio.render.v2` intent and a separate VoiceRender RenderProfile to orchestrate specialist speech packages. SSMDConvert owns `.ssmd-bundle` workspaces and their book/document manifest kind; specialist APIs own stage schemas, validation, persisted artifacts, and detailed diagnostics. Readio provides unified workflow sequencing and direct semantic speech without hidden workspace state.

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

Readio requires Python 3.10 or newer. Install it with `python -m pip install readio`, then install a speech engine through VoiceRender, for example `python -m pip install "readio[kokoro]"`. Optional `readio[documents]` and `readio[spacy]` extras enable document formats and UtterPlan spaCy support; AudioExport is included in the core installation.

## Readio commands

```bash
readio profile create --engine kokoro --target v1.0 --voice af_heart --language en-US -o narrator.renderprofile.toml
readio intent document create story.txt --profile narrator.renderprofile.toml --language en-US
readio render --config story.readio.toml --dry-run
readio render --config story.readio.toml
readio render story.txt --profile narrator.renderprofile.toml --bundle story.ssmd-bundle
readio status story.ssmd-bundle --profile narrator.renderprofile.toml
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello."
readio doctor --json
```

`readio render` uses temporary intermediate storage unless a persistent bundle is explicitly selected or supplied as the source. Specialist CLIs remain available for deliberate stage-level control; Readio does not provide a duplicate stage-runner namespace.
