# Readio

Readio is a thin orchestrator over specialist speech packages and native SSMDConvert `.ssmd-bundle` workspaces. The bundle manifest determines whether content represents a book or a document; Readio uses the same workflow for either kind and does not maintain a second project format.

Specialist packages own validation, schemas, persisted artifacts, reports, and kind-sensitive dispatch. Readio coordinates conversion, planning, RenderPlan projection, rendering, composition, and optional export. One-shot literal speech and local playback remain available.

## Install

```bash
python -m pip install readio
python -m pip install "readio[kokoro]"  # or another supported VoiceRender engine extra
python -m pip install "readio[audioexport]"  # optional export stage
```

`readio[documents]` enables SSMDConvert's optional PDF and DOCX support; `readio[spacy]` enables UtterPlan's optional spaCy support. Readio requires Python 3.10 or newer.

## Quick start

Convert a document source into an editable bundle, create a VoiceRender profile, and run the pipeline:

```bash
readio bundle convert chapter.docx -o chapter.ssmd-bundle
readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narrator.renderprofile.toml
readio bundle run chapter.ssmd-bundle \
  --profile narrator.renderprofile.toml \
  --language en-US
```

Books use the same bundle suffix and lifecycle. Keeping the RenderProfile in the bundle also makes its provenance available during projection:

```bash
readio bundle convert novel.epub -o novel.ssmd-bundle
readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o novel.ssmd-bundle/narrator.renderprofile.toml
readio bundle run novel.ssmd-bundle \
  --profile novel.ssmd-bundle/narrator.renderprofile.toml \
  --chapters 5-17 --language en-US --export --output novel.m4b
```

`readio bundle run` compiles, projects a RenderPlan, renders, and composes in order. If `--export` is selected, Readio also calls AudioExport. It forwards UtterPlan's exact projected variant unchanged to every downstream stage. AudioExport is optional; install `readio[audioexport]` to use it.

Run from a source file directly with `--bundle-output` when you want to choose the output directory:

```bash
readio bundle run chapter.docx \
  --bundle-output chapter.ssmd-bundle \
  --profile narrator.renderprofile.toml \
  --language en-US
```

To operate one stage at a time, use `readio bundle convert`, `compile`, `render-plan`, `render`, `compose`, or `export`. See the [CLI reference](docs/cli.md).

Inspect current state before continuing or resuming manually:

```bash
readio bundle status novel.ssmd-bundle --chapters 5-17
readio -vv bundle status novel.ssmd-bundle --json
```

Status is read-only, derives state from specialist APIs, and prints the next safe command when one can be identified. Verbose diagnostics and monotonic command/stage timing go to stderr; JSON results stay on stdout.

## Manual specialist workflow

Use specialist CLIs for package-specific controls. Preserve UtterPlan's exact variant through every stage:

```bash
ssmdconvert bundle convert novel.epub -o novel.ssmd-bundle
utterplan bundle compile novel.ssmd-bundle
voicerender profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o novel.ssmd-bundle/narrator.renderprofile.toml
utterplan bundle render novel.ssmd-bundle \
  --profile novel.ssmd-bundle/narrator.renderprofile.toml

VARIANT="<exact variant returned by utterplan bundle render>"
voicerender bundle render novel.ssmd-bundle --variant "$VARIANT"
audiocompose bundle compose novel.ssmd-bundle --variant "$VARIANT"
audioexport bundle export novel.ssmd-bundle --variant "$VARIANT" --output novel.m4b
```

## Direct speech

```bash
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello from Readio."
```

`speak` sends literal text through VoiceRender and plays the returned audio locally by default. Use `--output FILE.wav --no-play` to save without playback. Readio also retains playback and audio I/O helpers for Python callers.

## ONNX Runtime controls

Inspect the ONNX Runtime distribution/build and provider policy, or choose providers for future compatible sessions:

```bash
readio runtime status
readio runtime list
readio runtime use cpu
readio runtime use cuda
readio runtime use auto
readio runtime use cuda,cpu
readio runtime reset
```

An installed ONNX Runtime distribution/build is distinct from its execution providers. `runtime status` reports the imported build and its available providers; `runtime list` shows detected distributions and providers reported by that build. A selected/resolved provider policy is for new sessions, not proof of an active inference session. Readio does not install, uninstall, or switch ONNX Runtime distributions; package changes are managed outside Readio. Environment variables such as `ONNXVOICE_PROVIDER` override the saved preference, and `runtime reset` does not unset environment variables. Explicit `--engine-option providers=...` or a provider configured in a RenderProfile remains an intentional override of the global default.

## Python API

```python
from pathlib import Path
from readio.api import PlanRequest, Readio, RenderPlanRequest

service = Readio()
workspace = service.create_workspace("chapter.txt")
compiled = service.plan(workspace, PlanRequest(language="en-US"))
projection = service.render_plan(
    workspace,
    RenderPlanRequest(profile=Path("narrator.renderprofile.toml")),
)
print(workspace.root, projection.variant)
```

`WorkspaceRef.kind` is informational. Specialist bundle APIs perform operational kind dispatch. Use `Readio.run()` and `RunRequest` for complete sequencing, or call stage methods individually. The service preserves the exact projected variant and provides one kind-neutral `ExportRequest`. See the [Python API guide](docs/api.md).

## Documentation

- [Architecture and ownership](docs/architecture.md)
- [CLI reference](docs/cli.md)
- [Python API](docs/api.md)
- [Examples](examples/README.md)
- [Changelog](docs/changelog.md)
