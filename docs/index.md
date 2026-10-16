# Readio documentation

Readio is a thin convenience layer over specialist speech and audiobook packages. For package-specific controls, use the owning specialist CLI directly. Readio's main addition is orchestration across the `.ssmdbook` stages and a direct literal-text speech command.

```{toctree}
:maxdepth: 1

architecture
cli
api
changelog
```

## Specialist workflow

The manual workflow makes package ownership explicit:

```text
SSMDConvert → UtterPlan → VoiceRender → AudioCompose → AudioExport
```

Start with the [architecture guide](architecture.md) and use the owning tool's documentation for detailed package options. Readio provides the [CLI shorthand](cli.md) and a small [Python facade](api.md).

## Installation

Readio requires Python 3.10 or newer. Install Readio with `python -m pip install readio`, then install a synthesis backend through VoiceRender, for example `python -m pip install "voicerender[kokoro]"`. Readio does not provide engine extras or pin backend implementations.

Optional Readio extras only pass through specialist functionality: `readio[spacy]` enables UtterPlan's spaCy support, and `readio[documents]` enables SSMDConvert's PDF and DOCX support.

## Readio commands

```bash
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello from Readio."
readio audiobook convert novel.epub --output novel.ssmdbook
readio audiobook build novel.ssmdbook --profile narration.renderprofile.toml
readio doctor
```

`speak` accepts literal text and plays VoiceRender's returned audio through the current device. Audiobook conversion and build delegate to the specialist public APIs. The specialist packages own all persistent workspace artifacts and stage decisions.

## No legacy migration

Version 0.5.0 removes Readio-owned generic projects, `.readio` state, `project.json`, and the old plan, render-manifest, and pipeline APIs. There is no migration command or compatibility layer. Readio does not read or attach that state. Convert source material to an SSMDConvert-owned `.ssmdbook` workspace, or continue using the specialist tools directly.
