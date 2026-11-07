# Python API

`readio.api` is the user-facing orchestration gateway. Its primary workflow methods are `Readio.speak()`, `Readio.inspect(RenderRequest)`, and `Readio.render(RenderRequest)`. Specialist packages own their stage APIs, schemas, validation, artifacts, and reports; Readio normalizes workflow planning and execution without re-exporting stage request types as its primary API.

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

`RenderRequest` also accepts chapter selection, language, output format/path, bitrate, sample rate, channels, and specialist-backed option groups (`PlanningOptions`, `VoiceRenderOptions`, `CompositionOptions`, and `ExportOptions`). `rebuild_from` requests an explicit workflow rebuild; `replace_output` separately authorizes replacement of an existing final output. The dry-run form is `service.render(request, dry_run=True)` and returns the same `WorkflowPlan` shape as `inspect`.

The returned `WorkflowPlan` contains the source, workspace mode/path, selected variant and output, planned steps, and warnings. `RenderResult` reports the final output, workspace mode, exact variant, executed/reused workflow stages, and warnings. Normalized `WorkflowEvent` progress is available through the `on_progress` callback.

## Render intents

`RenderIntent` and the TOML helpers represent user-owned desired configuration. The current schema is `readio.render.v2`; input source and scope are separate from workspace policy:

```toml
schema = "readio.render.v2"
kind = "document"

[input]
source = "article.docx"
language = "en-US"

[workspace]
mode = "temporary"

[voice]
profile = "narrator.renderprofile.toml"
```

For persistence, use `mode = "bundle"` and provide `workspace.bundle`. The `create_book_intent` and `create_document_intent` factories apply kind-specific defaults; `load_render_intent`, `save_render_intent`, `parse_render_intent`, and `dump_render_intent` read, validate, and serialize the intent. A VoiceRender RenderProfile remains a separate, user-owned artifact.

```python
from pathlib import Path
from readio.api import create_document_intent, save_render_intent

intent = create_document_intent(
    Path("article.docx"),
    profile=Path("narrator.renderprofile.toml"),
    bundle=Path("article.ssmd-bundle"),
)
save_render_intent(intent, Path("article.readio.toml"))
```

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
