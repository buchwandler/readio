# Readio

Readio is a thin orchestrator over specialist speech packages and native SSMDConvert workspaces. It creates and resolves `.ssmdoc` and `.ssmdbook` bundles, then delegates planning, RenderPlan projection, synthesis, composition, and export to the package that owns each stage. One-shot literal speech and local playback remain available.

Readio does not maintain a second project directory, settings file, stage manifest, RenderPlan, or audio-job format. Specialist workspaces, artifacts, reports, and stage decisions are authoritative.

## Install

```bash
python -m pip install readio
python -m pip install "readio[kokoro]"  # or another supported VoiceRender engine extra
python -m pip install "readio[audioexport]"  # optional export stage
```

`readio[documents]` enables SSMDConvert's optional PDF and DOCX support; `readio[spacy]` enables UtterPlan's optional spaCy support. Backend engines are provided by VoiceRender extras. Readio requires Python 3.10 or newer.

## Quick start

Create a native document workspace, create a VoiceRender profile, and run the pipeline:

```bash
readio init chapter.docx -o chapter.ssmdoc
readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narrator.renderprofile.toml
readio run chapter.ssmdoc --profile narrator.renderprofile.toml --language en-US
```

For a book, convert the EPUB first. Keeping its RenderProfile in the workspace also makes its provenance available to UtterPlan:

```bash
readio init novel.epub -o novel.ssmdbook
readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o novel.ssmdbook/narrator.renderprofile.toml
readio run novel.ssmdbook --profile novel.ssmdbook/narrator.renderprofile.toml \
  --chapters 5-17 --language en-US --export --output novel.m4b
```

`run` compiles, projects the RenderPlan, renders, and composes in order. If export is selected, Readio also runs AudioExport. It forwards UtterPlan's exact projected variant unchanged to every downstream stage. To operate one stage at a time or inspect the available options, see the [CLI reference](docs/cli.md).

## Manual specialist workflow

Use the specialist CLIs when you need package-specific controls. Preserve UtterPlan's exact variant through every stage:

```bash
ssmdconvert book convert novel.epub -o novel.ssmdbook
utterplan compile-book novel.ssmdbook
voicerender profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o novel.ssmdbook/narrator.renderprofile.toml
utterplan render-book novel.ssmdbook \
  --profile novel.ssmdbook/narrator.renderprofile.toml

VARIANT="<exact variant returned by utterplan render-book>"
voicerender render-book novel.ssmdbook --variant "$VARIANT"
audiocompose compose-book novel.ssmdbook --variant "$VARIANT"
audioexport export-book novel.ssmdbook --variant "$VARIANT" --output novel.m4b
```

## Readio shorthand

### Direct speech

```bash
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello from Readio."
```

`speak` sends literal text through VoiceRender and plays the returned audio locally by default. Use `--output FILE.wav --no-play` to save without playback. Readio also retains playback and audio I/O helpers for Python callers.

## Python API

```python
from pathlib import Path
from readio.api import PlanRequest, Readio, RenderPlanRequest

service = Readio()
workspace = service.create_workspace("chapter.txt")  # writes chapter.ssmdoc
compiled = service.plan(workspace, PlanRequest(language="en-US"))
projection = service.render_plan(
    workspace,
    RenderPlanRequest(profile=Path("narrator.renderprofile.toml")),
)
print(workspace.root, projection.variant)
```

Use `Readio.run()` and `RunRequest` for complete sequencing. The service returns specialist results and preserves the exact UtterPlan-projected variant. See the [Python API guide](docs/api.md).

## Breaking change in v0.5.0

SSMDConvert owns conversion and workspace bundles; UtterPlan owns semantic plans and variants; RenderPlan and VoiceRender own their public contracts, profiles, and synthesis; AudioCompose owns composition; AudioExport owns encoding. Readio coordinates these public APIs and does not reproduce their schemas or validations.

Version 0.5 is a deliberate clean break. Existing Readio project directories, settings, plans, manifests, and stage records are unsupported; there is no migration command or compatibility layer. Start from source material and create a native workspace with `readio init`, or use the specialist packages directly.

## Documentation

- [Architecture and ownership](docs/architecture.md)
- [CLI reference](docs/cli.md)
- [Python API](docs/api.md)
- [Changelog](docs/changelog.md)
- [Examples](examples/README.md)
