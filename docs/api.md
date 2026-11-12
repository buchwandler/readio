# Python API

`readio.api` is the user-facing orchestration gateway. Its primary workflow methods are `Readio.speak()`, `Readio.inspect(RenderRequest)`, and `Readio.render(RenderRequest)`. Specialist packages own their stage APIs, schemas, validation, artifacts, and reports; Readio normalizes workflow planning and execution without re-exporting stage request types as its primary API.

## Source inspection

`inspect_source()` and `Readio.inspect_source()` expose SSMDConvert's normalized, read-only source inspection. They return a `SourceInspection` with the resolved source path, kind, metadata, and (for books) a tuple of `SourceChapter` records. This does not create a bundle or render workspace.

```python
from pathlib import Path
from readio.api import Readio

inspection = Readio().inspect_source(Path("novel.epub"))
print(inspection.kind, inspection.metadata)
for chapter in inspection.chapters:
    print(chapter.number, chapter.title, chapter.source_id)
```

Use `inspect_source()` when a caller needs source metadata or chapter inventory before deciding which workflow to request. Use `Readio.inspect(RenderRequest)` to inspect specialist workflow state; it remains read-only as well.

## Semantic speech

```python
from readio.api import Readio, SpeakRequest

service = Readio()
result = service.speak(
    SpeakRequest(text="Hello from Readio.", voice="kokoro:v1.0/af_heart"),
    play=False,
)
print(result.audio.sample_rate, result.audio.channels, len(result.audio))
```

Semantic speech returns an `AudioBuffer` in memory. Playback is optional, and this path does not create a bundle or intermediate artifacts. The `readio.api.speak(...)` convenience function is also available.

## Complete file-to-audio workflow

```python
from pathlib import Path
from readio.api import Readio, RenderRequest

service = Readio()
request = RenderRequest(
    source=Path("article.docx"),
    profile=Path("narrator.renderprofile.toml"),
    bundle=None,  # temporary workspace by default
)
plan = service.inspect(request)
print([(step.stage, step.action, step.state) for step in plan.steps])

result = service.render(request)
print(result.output, result.workspace_mode, result.variant)
```

`inspect` is read-only: it does not create a temporary directory, convert source material, or mutate specialist artifacts. `render` executes the complete workflow and cleans temporary state on success or failure. To persist an editable SSMD workspace, set `bundle=Path("article.ssmd-bundle")`; passing an existing `.ssmd-bundle` as `source` also selects persistent mode. There is no implicit default bundle path.

`RenderRequest` also accepts chapter selection, language, output format/path, bitrate, sample rate, channels, Readio-owned preparation/projection options, and specialist-backed option groups (`PlanningOptions`, `VoiceRenderOptions`, `CompositionOptions`, and `ExportOptions`). `rebuild_from` requests an explicit workflow rebuild; `replace_output` separately authorizes replacement of an existing final output. The dry-run form is `service.render(request, dry_run=True)` and returns the same `WorkflowPlan` shape as `inspect`.

The returned `WorkflowPlan` contains the source, workspace mode/path, selected variant and output, planned steps, and warnings. `RenderResult` reports the final output, workspace mode, exact variant, executed/reused workflow stages, and warnings. Normalized `WorkflowEvent` progress is available through the `on_progress` callback.

`on_progress` callbacks run synchronously. An executed stage emits `stage.started`, zero or more normalized specialist events, then `stage.completed`. A reused stage emits only `stage.reused` and does not call its specialist operation. Progress events preserve specialist `current` and `total` counters, `kind`, and `elapsed_seconds` when supplied. Additional data is available in `WorkflowEvent.details`; chapter identifiers are normalized to `chapter_id`, `chapter_number`, and `chapter_title` when the event is clearly chapter-scoped. Generic counters are not chapter numbers.

Readio does not synthesize workflow timing or ETA. AudioExport's public `export_bundle` API has no progress callback, so export has no intermediate counters or current-stage ETA. Consumers may still track their own total wall-clock time around `render()`.

## Source preparation, projection, and spaCy resources

`PreparationOptions` configures SSMDConvert conversion. Select a speech profile explicitly; SSMDConvert loads and validates it and applies its own replacement precedence. A concrete preparation spaCy tier is also applied to UtterPlan planning. `ProjectionOptions.pause_timing` selects `fixed`, `natural`, or `engaging` RenderPlan projection timing. It does not configure AudioCompose pause policy.

```python
from pathlib import Path
from readio.api import PreparationOptions, ProjectionOptions, RenderRequest

request = RenderRequest(
    source=Path("article.docx"),
    profile=Path("narrator.renderprofile.toml"),
    preparation=PreparationOptions(
        speech_semantics="preserve",
        spacy="md",
        speech_profile=Path("reader.speech.toml"),
        pronunciations={"Readio": "ree-dee-oh"},
    ),
    projection=ProjectionOptions(pause_timing="engaging"),
)
```

Use `list_spacy_models(language)` to inventory the concrete `sm`, `md`, `lg`, and `trf` tiers. It returns `SpacyModelInfo` records in that stable order with each resolved package name, package installation state, model version, and spaCy version when known. It reads distribution metadata only. It does not load models, download packages, install spaCy, or mutate the environment.

```python
from readio.api import list_spacy_models

for model in list_spacy_models("en-US"):
    print(model.tier, model.model, model.installed, model.model_version, model.spacy_version)
```

Use `inspect_spacy_model(language, tier)` to validate a selected model; this stronger check can load the model. Pass `offline=True` to `ensure_spacy_model(language, tier)` to fail rather than provision when the model is missing. Provisioning occurs only when `ensure_spacy_model()` is explicitly called. Source conversion, compilation, and projection do not download models. Install the optional `readio[spacy]` extra to enable spaCy support.

## Render intents

`RenderIntent` and the TOML helpers represent user-owned desired configuration. The current schema is `readio.render.v3`; input source and scope are separate from workspace policy:

```toml
schema = "readio.render.v3"
kind = "document"

[input]
source = "article.docx"
language = "en-US"

[workspace]
mode = "temporary"

[voice]
profile = "narrator.renderprofile.toml"
[preparation]
speech_semantics = "freeze"
spacy = "md"
speech_profile = "reader.speech.toml"

[preparation.pronunciations]
"Readio" = "ree-dee-oh"

[projection]
pause_timing = "engaging"
```

For persistence, use `mode = "bundle"` and provide `workspace.bundle`. `PreparationIntent` and `ProjectionIntent` carry source-preparation and RenderPlan timing choices; omitted preparation spaCy retains legacy planning settings. The `create_book_intent` and `create_document_intent` factories apply kind-specific defaults; `load_render_intent`, `save_render_intent`, `parse_render_intent`, and `dump_render_intent` read, validate, and serialize the intent. Version 2 files remain readable with no speech profile and fixed pause timing, and inspection does not rewrite them. Paths are resolved relative to the intent file when `config_path` is supplied. A VoiceRender RenderProfile remains a separate, user-owned artifact.

```python
from pathlib import Path
from readio.api import (
    PreparationIntent,
    ProjectionIntent,
    create_document_intent,
    save_render_intent,
)

intent = create_document_intent(
    Path("article.docx"),
    profile=Path("narrator.renderprofile.toml"),
    bundle=Path("article.ssmd-bundle"),
    preparation=PreparationIntent(
        spacy="md",
        speech_profile=Path("reader.speech.toml"),
    ),
    projection=ProjectionIntent(pause_timing="engaging"),
)
save_render_intent(intent, Path("article.readio.toml"))
```

## Converting and running saved intents

`render_request_from_intent()` resolves a saved `RenderIntent` and maps its values to the canonical `RenderRequest`. Paths in the intent are resolved relative to its config file when `config_path` is provided. The bridge does not execute specialist stages.

```python
from pathlib import Path
from readio.api import Readio, load_render_intent, render_request_from_intent

intent_path = Path("article.readio.toml")
intent = load_render_intent(intent_path)
request = render_request_from_intent(intent, config_path=intent_path)
service = Readio()

plan = service.inspect(request)
result = service.render(request)
```

`service.request_from_intent()` is the instance equivalent. `service.inspect_intent()` and `service.render_intent()` are convenience wrappers over the same request bridge and canonical workflows. For example, `service.render_intent(intent, dry_run=True)` returns a `WorkflowPlan`; it does not run conversion or rendering. Persistent book requests convert the full source inventory, while chapter selection limits downstream processing.

## Management delegates

`readio.api` also exposes delegates for VoiceRender engine/model discovery, voice discovery and source/clone operations, selector parsing, and RenderProfile creation, validation, refresh, and role management. These calls preserve specialist results and validation behavior.

ONNX Runtime controls delegate to VoiceRender's public package-root APIs:

```python
from readio.api import reset_runtime_providers, runtime_status, set_runtime_providers

status = runtime_status()
set_runtime_providers("cuda,cpu")
status = runtime_status()  # policy and resolution for future sessions
status = reset_runtime_providers()
```

`runtime_status()` describes the imported distribution/build, available providers, configured policy and its source, and resolution for new sessions; it does not report a provider active in an initialized synthesis session. A distribution/package is distinct from an execution provider. Environment variables such as `ONNXVOICE_PROVIDER` override the saved preference, and reset does not unset them. Readio does not install or switch runtime distributions.
