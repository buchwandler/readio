# Readio

Readio is a workflow gateway over specialist speech packages. It orchestrates complete text-to-audio and file-to-audio workflows, using a run-scoped temporary workspace by default or an explicitly requested persistent SSMDConvert `.ssmd-bundle`. User-owned render intents describe the requested workflow without duplicating specialist schemas or creating hidden `.readio/` state.

Specialist packages own validation, schemas, persisted artifacts, reports, and kind-sensitive dispatch. Readio coordinates conversion, planning, RenderPlan projection, rendering, composition, and optional export. One-shot literal speech and local playback remain available.

## Install

```bash
python -m pip install readio
python -m pip install "readio[kokoro]"  # or another supported VoiceRender engine extra
```

`readio[documents]` enables SSMDConvert's optional PDF and DOCX support; `readio[spacy]` enables UtterPlan's optional spaCy support. Readio requires Python 3.10 or newer.

Use `readio.api.inspect_spacy_model(language, tier)` to check a local model and `readio.api.ensure_spacy_model(language, tier)` to explicitly provision one. Readio conversion and UtterPlan stages do not download models. `PreparationOptions` controls conversion speech semantics, spaCy tier, and explicit speech profiles; `ProjectionOptions` controls RenderPlan pause timing separately from AudioCompose pause policy.

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

Book intents default to M4B and support chapter selection; document intents default to MP3. Without an explicit bundle, renders use temporary intermediate storage. Specify `--bundle PATH` in the intent or render command to persist an editable workspace; an existing bundle supplied as the source is also persistent. UtterPlan's exact projected variant is preserved through VoiceRender, AudioCompose, and AudioExport.

```bash
readio intent book create novel.epub \
  --profile narrator.renderprofile.toml --language en-US --chapters 5-17 \
  --bundle novel.ssmd-bundle
readio render --config novel.readio.toml
```

### Create profiles from SSMD voice roles

For an SSMD document with one explicit `narrator` role, inspect and create its profile directly:

```bash
readio profile document inspect chapter.ssmd.md
readio profile document create chapter.ssmd.md \
  --engine kokoro --target v1.0 --language en-US \
  --voice narrator=af_heart \
  -o chapter.renderprofile.toml
```

For a multi-speaker source, assign each logical role in one command. Logical roles stay distinct even when they intentionally share one physical TTS voice:

```bash
readio profile document inspect meeting.ssmd.md
readio profile document create meeting.ssmd.md \
  --engine kokoro --target v1.0 --language en-US \
  --voice narrator=af_heart \
  --voice host=af_sarah \
  --voice guest=am_michael \
  --same-as analyst=guest \
  --default-role narrator \
  -o meeting.renderprofile.toml

readio intent document create meeting.ssmd.md \
  --profile meeting.renderprofile.toml --language en-US
readio intent show meeting.readio.toml --resolved
```

Here four logical roles use three physical voices: `analyst` receives a copy of `guest`'s complete binding. The reviewable VoiceRender RenderProfile remains separate from the Readio intent; Readio does not choose voices or create a profile automatically.

For a one-off render without saving a Readio intent, provide the source and separate RenderProfile directly:

```bash
readio render chapter.docx --profile narrator.renderprofile.toml --language en-US
```

`readio render --dry-run` and `readio status` share a read-only workflow inspection; neither creates a temporary workspace or mutates artifacts. Use `--rebuild-from STAGE` for explicit downstream rebuilds and `--replace-output` separately to authorize replacing an existing final output. AudioExport is included in the core installation; `--ffmpeg` and `--ffprobe` select tool executables.

Intent files are visible user-owned configuration, not hidden workspace state. Readio creates no `.readio/` directory. `readio status --config novel.readio.toml` inspects a saved intent, while `readio status novel.ssmd-bundle --profile narrator.renderprofile.toml --chapters 5-17` inspects an existing persistent workspace.

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
from readio.api import Readio, RenderRequest

service = Readio()
request = RenderRequest(
    source=Path("chapter.docx"),
    profile=Path("narrator.renderprofile.toml"),
    bundle=None,  # temporary by default; pass a path to persist the SSMD bundle
)
plan = service.inspect(request)  # read-only WorkflowPlan
result = service.render(request)
print(plan.steps, result.output, result.workspace_mode)
```

The primary gateway API is `Readio.speak()`, `Readio.inspect(RenderRequest)`, and `Readio.render(RenderRequest)`. `speak` synthesizes semantic text in memory; `inspect` reports specialist-backed workflow state without writing; `render` executes the complete file-to-audio workflow. Specialist CLIs remain available for deliberate package-level debugging. See the [Python API guide](docs/api.md).

Source inventory and saved intents use the same gateway:

```python
from pathlib import Path
from readio.api import Readio, load_render_intent, render_request_from_intent

service = Readio()
source = service.inspect_source(Path("novel.epub"))
intent_path = Path("novel.readio.toml")
intent = load_render_intent(intent_path)
request = render_request_from_intent(intent, config_path=intent_path)
plan = service.inspect(request)
```

`inspect_source()` is read-only and does not create a workspace. `inspect_intent()` and `render_intent()` are convenience methods over the same `RenderRequest` mapping and canonical inspect/render workflow. Use `service.render_intent(intent, dry_run=True)` for a read-only workflow plan.

## Documentation

- [Architecture and ownership](docs/architecture.md)
- [CLI reference](docs/cli.md)
- [Python API](docs/api.md)
- [Examples](examples/README.md)
- [Changelog](docs/changelog.md)
