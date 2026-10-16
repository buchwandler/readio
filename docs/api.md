# Python API

The supported facade is `readio.api`. It contains a small set of orchestration and direct-speech operations. Specialist APIs and result objects remain authoritative.

```python
from readio.api import (
    BookBuildRequest,
    BookBuildResult,
    BookProgressEvent,
    VoiceSelector,
    build_book,
    convert_book,
    open_book,
    parse_voice_selector,
    speak,
)
```

## Book operations

`convert_book(source, output=None, *, chapters="all", language=None, on_progress=None)` converts an EPUB through SSMDConvert and writes an SSMDConvert-owned `.ssmdbook` workspace. It returns the specialist `BookWorkspace` object.

`open_book(workspace)` opens a workspace through SSMDConvert without initializing Readio state. It returns the specialist workspace object.

A full build uses the small Readio request type and passes each stage through the owning package's public API:

```python
from pathlib import Path

from readio.api import BookBuildRequest, build_book, convert_book

workspace = convert_book("novel.epub", "novel.ssmdbook", chapters="5-17")
result = build_book(
    BookBuildRequest(
        workspace=workspace.path,
        profile=Path("narration.renderprofile.toml"),
        chapters="5-17",
        output=Path("novel.m4b"),
    )
)
print(result.variant)
```

`BookBuildRequest` accepts:

- `workspace`: an existing specialist-produced `.ssmdbook` workspace.
- `profile`: a VoiceRender RenderProfile path when RenderPlan projection is enabled.
- `chapters`, `force`, and `fail_fast`: forwarded to stage APIs where supported.
- `compile`, `render_plan`, `synthesize`, `compose`, and `export`: select build stages.
- `variant`: an existing specialist variant when projection is skipped.
- `stop_after`: stop after `compile`, `render-plan`, `synth`, `compose`, or `export`.
- `output`, `export_profile`, `metadata`, `cover`, and `bitrate`: forwarded to AudioExport.

`build_book` returns a `BookBuildResult` containing the workspace path, exact variant, and the actual specialist result objects for any executed stages. It does not copy specialist result models or persist Readio stage state. `on_progress` receives small Readio `BookProgressEvent(stage, kind)` values for stage start and completion. Specialist exceptions propagate unchanged.

## Direct speech

`speak(text, *, voice=None, engine=None, target=None, language="en-US", speaker=None, offline=False, refresh=False, engine_options=None)` resolves and synthesizes through VoiceRender, then plays the returned audio through the current device.

```python
from readio.api import parse_voice_selector, speak

selector = parse_voice_selector("kokoro:v1.0/af_heart")
assert selector.engine == "kokoro"
assert selector.target_id == "v1.0"
assert selector.voice == "af_heart"

rendered = speak(
    "A short sentence.",
    voice="kokoro:v1.0/af_heart",
    language="en-US",
)
```

The selector syntax is neutral. Readio does not maintain an engine registry or backend alias table. VoiceRender owns engine selection, session lifecycle, capacity handling, and synthesis. The returned value is the specialist rendered-speech object.

## Compatibility policy

The former Readio project, plan, engine, synthesis, composition, export, catalog, verification, and integration API types have been removed. There are no compatibility aliases, `.readio` project migrations, or adapters for old result models. Use an SSMDConvert `.ssmdbook` workspace and call the specialist package responsible for each stage.
