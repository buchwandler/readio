# Python API

`readio.api` is the user-facing orchestration gateway. `Readio.synthesize()` is the canonical application speech method and returns in-memory audio without playback; `Readio.speak()` is its optional-playback convenience wrapper. The file-to-audio workflow remains `Readio.inspect(RenderRequest)` and `Readio.render(RenderRequest)`. Specialist packages own their stage APIs, schemas, validation, artifacts, and reports; Readio normalizes workflow planning and execution without re-exporting stage request types as its primary API.

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
request = SpeakRequest(text="Hello from Readio.", voice="kokoro:v1.0/af_heart")
result = service.synthesize(request)
wav_bytes = result.audio.to_wav_bytes()
print(
    result.audio.sample_rate,
    result.audio.channels,
    result.audio.frames,
    result.duration_seconds,
)
```

`Readio.synthesize()` returns a `SpeakResult` containing an `AudioBuffer`. It does not play audio, write files, or persist workflow artifacts. Use `result.audio.write_wav(path)` when a WAV file is explicitly needed. `Readio.speak(request, play=True)` delegates to synthesis and optionally plays the result; the module-level `readio.api.speak(...)` remains available for compatibility.

Pass a `CancellationToken` to `synthesize()` when the application needs cooperative cancellation. Readio checks the token at stage boundaries. It does not interrupt a VoiceRender engine call already in progress.

## Application catalog and errors

Use the `Readio` instance methods for normalized application catalog records. They return Readio-owned DTOs rather than VoiceRender descriptor objects:

```python
engines = service.list_engines()
targets = service.list_targets(engines[0].id, language="en-US", offline=True)
voices = service.list_voices(
    engine=engines[0].id,
    target=targets[0].id,
    language="en-US",
    offline=True,
)
options = service.list_engine_options(engines[0].id, target=targets[0].id)
```

`CatalogError` wraps expected failures from normalized discovery and option inspection. Use `describe_error()` to project any expected `ReadioError` into the stable `ErrorInfo` DTO for application transport. The module-level discovery delegates documented below remain raw compatibility APIs and preserve specialist results.

## Engine model inventory and lifecycle

Use the `Readio` gateway for normalized engine-model records and explicit lifecycle actions. Its public methods are `Readio.list_models()`, `Readio.model_status()`, `Readio.install_model()`, `Readio.update_model()`, `Readio.verify_model()`, and `Readio.remove_model()`. The inventory is read-only: it does not install, download, load, verify, or inspect engine caches.

```python
from readio.api import Readio

service = Readio()
models = service.list_models("kitten", language="en-US", offline=True)
status = service.model_status("kitten", "kitten-v1", quality="high")
```

`EngineModelInfo` is a Readio-owned DTO with `ModelState` and `ModelUpdateState` fields. `state` is mapped only from recognized, consistent VoiceRender status values; unknown or inconsistent status remains `unknown`. `update_state` is `unknown` when no authoritative comparison was reported. Version strings are provider-defined, and byte counts or storage identity remain `None` when the provider does not report them. False availability/action flags mean VoiceRender did not assert that capability, not that Readio independently checked it.

`install_model()`, `verify_model()`, and `remove_model()` are explicit operations delegated to VoiceRender. They return normalized status, re-querying when an operation result has no usable model descriptor. The current VoiceRender public API has no authoritative update operation, so `update_model()` raises `ModelOperationError`; it never aliases to force-install. Existing module-level `list_engine_models()`, `inspect_engine_model()`, and operation delegates remain specialist-result compatibility APIs.

`engine_models_to_dict()` and `serialize_public()` use the versioned `readio.engine-models.v1` envelope, containing only normalized Readio fields.

### VoiceRender 0.2.12 compatibility matrix

| VoiceRender public `ModelArtifact` field                                                                         | Readio projection                                        | Current support                               |
| ---------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- | --------------------------------------------- |
| `engine`                                                                                                         | `engine`                                                 | Available                                     |
| `target_id`                                                                                                      | `target`                                                 | Available                                     |
| `display_name`                                                                                                   | `label`                                                  | Available                                     |
| `installed`                                                                                                      | `installed` and consistency check for `state`            | Available                                     |
| `status`                                                                                                         | `state` for recognized exact values, otherwise `unknown` | Available as an open string                   |
| `revision`                                                                                                       | `local_version` only when installed                      | Available, provider-defined                   |
| `languages`                                                                                                      | `language` only when exactly one language is reported    | Available                                     |
| `qualities`, `installed_qualities`                                                                               | No singular `quality` projection                         | Aggregate lists only                          |
| `source`                                                                                                         | No Readio field                                          | Available upstream, not part of DTO           |
| `path`                                                                                                           | No storage path or identity projection                   | Available upstream, intentionally not exposed |
| `metadata`                                                                                                       | Not parsed                                               | Open-ended upstream mapping, ignored          |
| available revision, update comparison, byte sizes, storage identity, per-model action support, progress callback | `None`, `unknown`, or unasserted action flags            | Not exposed by 0.2.12                         |

VoiceRender operation results can include an optional `artifact`; Readio normalizes it when present and otherwise queries model status. Readio does not inspect private VoiceRender modules, infer cache paths, or turn provider/network failures into `update_state="current"`.

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

`readio.api` retains module-level delegates for VoiceRender engine/model discovery, voice discovery and source/clone operations, selector parsing, and RenderProfile creation, validation, refresh, and role management. They preserve specialist results and validation behavior for compatibility. Application integrations should use `Readio.list_engines()`, `Readio.list_targets()`, `Readio.list_voices()`, and `Readio.list_engine_options()` for normalized Readio-owned records.

ONNX Runtime controls delegate to VoiceRender's public package-root APIs:

```python
from readio.api import reset_runtime_providers, runtime_status, set_runtime_providers

status = runtime_status()
set_runtime_providers("cuda,cpu")
status = runtime_status()  # policy and resolution for future sessions
status = reset_runtime_providers()
```

## Public API compatibility

Readio's public API follows additive minor-version compatibility: new public types and functions, defaulted dataclass fields, optional keyword-only parameters, and additional JSON fields may be added without changing existing meanings. Removing or renaming exports, changing established code or schema meanings, making optional data required, changing safety defaults or destructive authorization, or changing a JSON schema identity requires a major-version decision. Diagnostic `details` mappings and specialist internals are not stable integration contracts.

`runtime_status()` describes the imported distribution/build, available providers, configured policy and its source, and resolution for new sessions; it does not report a provider active in an initialized synthesis session. A distribution/package is distinct from an execution provider. Environment variables such as `ONNXVOICE_PROVIDER` override the saved preference, and reset does not unset them. Readio does not install or switch runtime distributions.

## Workflow, capability, and execution contracts

`Readio.inspect(RenderRequest)` returns the canonical `WorkflowPlan`; `Readio.render(request, dry_run=True)` presents that same plan. The stable JSON envelope is `readio.workflow-plan.v1`. Each step provides an action, a Readio-owned `reason_code`, blockers, and logical input/output artifact references. `WorkflowPlan.executable` is false if any step is blocked. Human-readable reasons and `WorkflowStep.details` are diagnostic context, not fields to parse for decisions. Stale or forced replacement remains blocked unless the request explicitly authorizes rebuilding and the specialist reports replacement as safe. Replacing an existing final output is a separate `replace_output=True` authorization.

`describe_error()` projects expected `ReadioError` instances into `ErrorInfo`, serialized as `readio.error-info.v1`. Stable codes include `invalid_request`, `catalog_operation_failed`, `unsafe_workflow_state`, `workflow_replan_failed`, `output_exists`, `stage_failed`, and `cancellation_requested`. Specialist codes, when useful, remain nested diagnostic details and do not become Readio error codes. The same `serialize_public()` layer selects versioned projections including `readio.workflow-plan.v1`, `readio.render-result.v1`, `readio.error-info.v1`, `readio.capabilities.v1`, `readio.source-capabilities.v1`, `readio.synthesis-capabilities.v1`, `readio.export-capabilities.v1`, and `readio.speak-result.v1`. `readio.render.v3` identifies the intent format; it is separate from these response schemas.

The read-only capability methods are `Readio.capabilities()`, `Readio.source_capabilities(path)`, `Readio.synthesis_capabilities()`, and `Readio.export_capabilities()`. They normalize current availability and do not create workspaces, provision dependencies, or download models. `Readio.source_capabilities(path)` describes one concrete source instead of claiming a global source-format catalog.

Pass a `CancellationToken` to `Readio.render()` or `Readio.synthesize()` for cooperative cancellation at Readio-controlled boundaries and supported specialist callbacks. Cancellation does not interrupt a specialist call already running. AudioExport has no public mid-export cancellation callback, so Readio cannot stop an export in progress. `Readio.render(request, include_report=True)` optionally returns an in-memory `ExecutionReport` with stage outcomes, monotonic durations, and best-effort specialist versions. Reports are not written to disk or retained as history.

## Optional MCP adapter

Install the official MCP SDK extra with `python -m pip install 'readio[mcp]'`, then run `python -m readio.mcp.server` under an MCP host using stdio. The server exposes `speech_inspect_source`, `speech_inspect_workflow`, `speech_capabilities`, `speech_list_engines`, `speech_list_voices`, `speech_render`, and `speech_speak`. Tool results use the shared public serializer. Voice listing is offline and cache-only by default. The render tool requires explicit source, profile, and output paths, inspects before execution, and never enables rebuild or output replacement unless the caller sets the corresponding controls. `speech_speak` synthesizes explicit text with explicit engine and target and returns a base64 WAV payload without playback. MCP remains optional and adds no hidden persistence, worker service, or specialist-private API usage.
