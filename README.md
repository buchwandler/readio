# Readio

Readio is an intent-first orchestrator over specialist speech packages and native SSMDConvert `.ssmd-bundle` workspaces. A user-owned Readio render intent describes a complete book or document render; the bundle manifest still determines content kind, and Readio creates no parallel project format or hidden workspace state.

Specialist packages own validation, schemas, persisted artifacts, reports, and kind-sensitive dispatch. Readio coordinates conversion, planning, RenderPlan projection, rendering, composition, and optional export. One-shot literal speech and local playback remain available.

## Install

```bash
python -m pip install readio
python -m pip install "readio[kokoro]"  # or another supported VoiceRender engine extra
python -m pip install "readio[audioexport]"  # optional export stage
```

`readio[documents]` enables SSMDConvert's optional PDF and DOCX support; `readio[spacy]` enables UtterPlan's optional spaCy support. Readio requires Python 3.10 or newer.

## Quick start

Create a VoiceRender RenderProfile, then create and review a user-owned Readio render intent. The two TOML files have separate responsibilities: the RenderProfile defines voice roles; the intent describes the requested conversion, planning, composition, and output.

```bash
readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narrator.renderprofile.toml
readio intent document create chapter.docx \
  --profile narrator.renderprofile.toml --language en-US
readio intent show chapter.readio.toml --resolved
readio intent validate chapter.readio.toml
readio render --config chapter.readio.toml --dry-run
readio render --config chapter.readio.toml
```

Book intents default to M4B and support chapter selection; document intents default to MP3. Both reuse existing `.ssmd-bundle` workspaces and preserve UtterPlan's exact projected variant through VoiceRender, AudioCompose, and AudioExport.

```bash
readio intent book create novel.epub \
  --profile narrator.renderprofile.toml --language en-US --chapters 5-17
readio render --config novel.readio.toml
```

For a one-off render without saving a Readio intent, provide the source and separate RenderProfile directly:

```bash
readio render chapter.docx --profile narrator.renderprofile.toml --language en-US
```

`readio render --dry-run` resolves paths and reports bundle/stage status without executing stages. Existing bundles are reused by default; use `--force-stage STAGE` or `--restart-from STAGE` for explicit reruns. Replacing an existing final output requires forcing `export`. AudioExport is optional (`pip install "readio[audioexport]"`); Readio preflights the selected format, FFmpeg, and FFprobe before conversion, with `--ffmpeg` and `--ffprobe` overrides.

Intent files are visible user-owned configuration, not hidden workspace state. Readio creates no `.readio/` directory. The independent `readio status --config novel.readio.toml` command reports derived state for a saved intent.

## Advanced bundle and stage workflows

`readio bundle run` remains available when you want to manage the lifecycle directly:

```bash
readio bundle convert chapter.docx -o chapter.ssmd-bundle
readio bundle run chapter.ssmd-bundle \
  --profile narrator.renderprofile.toml --language en-US

readio bundle convert novel.epub -o novel.ssmd-bundle
readio bundle run novel.ssmd-bundle \
  --profile novel.ssmd-bundle/narrator.renderprofile.toml \
  --chapters 5-17 --language en-US --export --output novel.m4b
```

Run from a source file directly with `--bundle-output` when you want to choose the output directory:

```bash
readio bundle run chapter.docx \
  --bundle-output chapter.ssmd-bundle \
  --profile narrator.renderprofile.toml \
  --language en-US
```

For stage-by-stage control, use `readio bundle convert`, `compile`, `render-plan`, `render`, `compose`, or `export`. `readio bundle status novel.ssmd-bundle` inspects an existing bundle; use `readio status --config novel.readio.toml` for an intent-based view. See the [CLI reference](docs/cli.md) for advanced options.

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
from readio.api import Readio, RenderExecutionOptions, create_document_intent

service = Readio()
intent = create_document_intent(
    Path("chapter.txt"),
    profile=Path("narrator.renderprofile.toml"),
    language="en-US",
)
plan = service.plan_render_intent(intent)
result = service.render_intent(intent, RenderExecutionOptions())
print(plan.resolved.output, result.variant)
```

For advanced stage-by-stage use, `Readio.run()` and `RunRequest` remain available, as do the individual stage methods. VoiceRender's RenderProfile stays separate from the Readio intent, and all downstream stages receive the exact projected variant. See the [Python API guide](docs/api.md).

## Documentation

- [Architecture and ownership](docs/architecture.md)
- [CLI reference](docs/cli.md)
- [Python API](docs/api.md)
- [Examples](examples/README.md)
- [Changelog](docs/changelog.md)
