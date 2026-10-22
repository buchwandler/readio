# Readio documentation

Readio's intent-first render workflow uses a visible, user-owned TOML request and a separate VoiceRender RenderProfile to orchestrate specialist speech packages. SSMDConvert owns `.ssmd-bundle` workspaces and their book/document manifest kind; specialist APIs own stage schemas, validation, persisted artifacts, and detailed diagnostics. Readio provides workflow sequencing, low-level stage commands, and direct literal-text speech without hidden workspace state.

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
readio profile create --engine kokoro --target v1.0 --voice af_heart --language en-US -o narrator.renderprofile.toml
readio intent document create story.txt --profile narrator.renderprofile.toml --language en-US
readio render --config story.readio.toml --dry-run
readio render --config story.readio.toml
readio bundle run story.txt --bundle-output story.ssmd-bundle --profile narrator.renderprofile.toml --language en-US
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello."
readio doctor --json

`readio render` is the intent-first complete workflow; `readio bundle run` and the individual `readio bundle` stage commands remain available for advanced control. Every workflow uses specialist-owned `.ssmd-bundle` state, and Readio persists no hidden `.readio/` workspace files.
```
