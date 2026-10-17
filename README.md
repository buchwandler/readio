# Readio

Readio 0.5.0 is a small speech and audiobook convenience layer over specialist packages, with a staged single-document renderer and delegated engine, model, voice, profile, and settings controls. SSMDConvert owns `.ssmdbook` workspaces, UtterPlan owns semantic compilation and RenderPlan projection, VoiceRender owns voice resolution and synthesis, AudioCompose owns composition, and AudioExport owns encoded exports. Readio coordinates those public APIs.

## Manual specialist workflow

The specialist commands are the detailed and canonical workflow. Install the stack and one VoiceRender engine extra:

```bash
python -m pip install \
  "ssmdconvert>=0.2.0,<0.3" \
  "utterplan>=0.6.3,<0.7" \
  "voicerender>=0.2.0,<0.3" \
  "renderplan>=0.1.2,<0.2" \
  "audiocompose>=0.3.0,<0.4" \
  "audioexport>=0.1.2,<0.2"
python -m pip install "voicerender[kokoro]>=0.2.0,<0.3"
```

Convert a book, compile it, and create a VoiceRender profile:

```bash
ssmdconvert book convert novel.epub --chapters 5-17 -o novel.ssmdbook
utterplan compile-book novel.ssmdbook --chapters 5-17
voicerender profile create \
  --engine kokoro \
  --target v1.0 \
  --voice af_heart \
  --language en-US \
  -o narration.renderprofile.toml
utterplan render-book novel.ssmdbook \
  --chapters 5-17 \
  --profile narration.renderprofile.toml
```

Use the exact variant returned by UtterPlan for every later stage:

```bash
VARIANT="<exact variant returned by utterplan render-book>"
voicerender render-book novel.ssmdbook --chapters 5-17 --variant "$VARIANT" \
  --emphasis-policy defer-to-compose
audiocompose compose-book novel.ssmdbook --chapters 5-17 --variant "$VARIANT"
audioexport export-book novel.ssmdbook --chapters 5-17 --variant "$VARIANT" \
  --output novel.m4b
```

Specialist artifacts and reports remain authoritative. Use specialist CLIs and profiles when you need package-specific controls or detailed diagnostics.

## Readio shorthand

Install Readio and the backend through VoiceRender, then speak literal text through the current audio device:

```bash
python -m pip install readio "voicerender[kokoro]>=0.2.0,<0.3"
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello from Readio."
```

Readio also renders one source document through the specialist pipeline and exposes delegated engine/profile controls:

```bash
readio render article.pdf --profile narration.renderprofile.toml \
  --output article.m4a --keep-artifacts
readio engines list
readio voices list
readio profile role list narration.renderprofile.toml
readio config init --output readio.toml
```

Settings are read only when explicitly selected with `--config PATH`; CLI flags override values from that file.

Readio can also run the book stages as one operation:

```bash
readio audiobook convert novel.epub --chapters 5-17 --output novel.ssmdbook
readio audiobook build novel.ssmdbook \
  --profile narration.renderprofile.toml \
  --chapters 5-17 \
  --output novel.m4b
```

See [`readio --help`](docs/cli.md) for the retained commands and [`readio.api`](docs/api.md) for Python use. `readio doctor` reports specialist package versions and VoiceRender engine availability.

## Installation options

Readio requires Python 3.10 or newer. Install only the core convenience layer with `python -m pip install readio`, then install a backend extra through VoiceRender, for example `voicerender[kokoro]`, `voicerender[piper]`, or `voicerender[inflect]`. Readio does not mirror backend packages as its own extras.

The `readio[spacy]` extra passes through UtterPlan's optional spaCy support. The `readio[documents]` extra passes through SSMDConvert's PDF and DOCX support. Neither is required for EPUB audiobook conversion or direct literal-text speech.

## Python API

```python
from readio.api import (
    BookBuildRequest,
    DocumentRenderRequest,
    build_book,
    convert_book,
    open_book,
    render_document,
    speak,
)

workspace = convert_book("novel.epub", "novel.ssmdbook", chapters="5-17")
book = open_book(workspace.path)
result = build_book(
    BookBuildRequest(
        workspace=book.path,
        profile="narration.renderprofile.toml",
        chapters="5-17",
        output="novel.m4b",
    )
)
print(result.variant)

document = render_document(
    DocumentRenderRequest(
        source="chapter.docx",
        profile="narration.renderprofile.toml",
        stop_after="render-plan",
        keep_artifacts=True,
    )
)
print(document.renderplan_path)

speak("A short sentence.", voice="kokoro:v1.0/af_heart", language="en-US")
```

`build_book` returns the original specialist result objects inside a small Readio orchestration result. Specialist exceptions are not translated into Readio-specific error codes.

## Breaking change in v0.5.0

This is a clean break. Readio-owned generic projects, `.readio` state, `project.json`, legacy plan and render-manifest formats, old project and pipeline commands, and the mirrored engine/runtime APIs have been removed. There is no migration command or runtime compatibility layer. Readio does not read or attach legacy project state. Start from source material and convert it to an SSMDConvert-owned `.ssmdbook` workspace, or use the specialist packages directly.

The old Readio engine, composition, and export implementations are gone. Install engine extras through VoiceRender, and use UtterPlan, AudioCompose, and AudioExport for their respective specialist operations. See the [v0.5.0 release notes](docs/changelog.md) for the full removal list.

## Documentation

- [Architecture and ownership](docs/architecture.md)
- [CLI reference](docs/cli.md)
- [Python API](docs/api.md)
- [Examples](examples/README.md)
- [Changelog](docs/changelog.md)
