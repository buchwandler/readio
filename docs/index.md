# Readio documentation

Readio orchestrates SSMDConvert `.ssmd-bundle` workspaces through specialist speech packages. The bundle manifest determines whether the content represents a book or a document; specialist APIs own stage schemas, validation, persisted artifacts, and detailed diagnostics. Readio provides workflow sequencing, a CLI, and direct literal-text speech.

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
readio bundle convert story.txt -o story.ssmd-bundle
readio bundle run story.ssmd-bundle --profile narrator.renderprofile.toml --language en-US
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello."
readio doctor --json
```

Every SSMD workflow uses the `.ssmd-bundle` format. Readio sequences the package-owned stages without duplicating specialist state or kind-sensitive routing.
