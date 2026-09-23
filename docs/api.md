# Python API

`readio.api` is Readio's supported, synchronous Python application boundary. Import application objects and stable request/result types from `readio.api`. Modules outside that namespace are implementation details unless explicitly documented as an extension contract.

The API does not depend on the CLI or TTSForge. It performs no printing, process exit, or implicit logging configuration. Calls block until their operation completes. Long-running services accept an `on_event` callback; GUI and orchestration clients can run a synchronous call in their own worker.

## Application and configuration

```python
from readio.api import Readio

app = Readio()  # load the normal user configuration
```

For an isolated application or custom configuration, pass an immutable `ReadioConfig`:

```python
from dataclasses import replace
from pathlib import Path

from readio.api import Readio, default_config

config = default_config()
config = replace(
    config,
    paths=replace(config.paths, output=Path("./output")),
)
app = Readio(config=config)
```

Services are created lazily on first access: `app.speech`, `app.projects`, `app.audiobooks`, `app.catalog`, `app.roles`, `app.ssmd`, `app.configuration`, `app.templates`, `app.ingest`, and `app.diagnostics`. Persisted configuration changes do not mutate an existing application's snapshot. Create a new `Readio` instance to use the saved values.

## One-shot speech

Construct a typed request instead of passing CLI arguments. The same request can be planned without synthesis, rendered to a file, played, or sent to a caller-owned sink.

```python
import json
from pathlib import Path

from readio.api import (
    InputRequest,
    OutputRequest,
    PlanRequest,
    Readio,
    SynthesisRequest,
    document_from_text,
)

app = Readio()
request = PlanRequest(
    operation="render",
    input=InputRequest(
        document=document_from_text("Hello from Readio."),
        requested_format="text",
    ),
    synthesis=SynthesisRequest(language="en-us"),
    output=OutputRequest(
        requested_format="wav",
        requested_path=Path("hello.wav"),
    ),
)

plan = app.speech.plan(request)  # metadata resolution only, no TTS model/session
print(json.dumps(plan.to_dict(), ensure_ascii=False))
if plan.ok:
    result = app.speech.render(request, write_manifest=True)
    print(result.output_path)
```

`document_from_file(path)` constructs an input document from a file. Choose `requested_format="markdown"` or `"ssmd"` when the input format is known, or leave it as `"auto"`. A successful bounded `render()` owns its output file and sink. It returns a typed `RenderResult`; when requested, the colocated render manifest is available as `result.manifest_path`.

For playback, call `app.speech.speak(request)`. It creates and closes the playback sink. For live text, `render_live(lines, sink, ...)` consumes but does not close the caller's iterable or sink. `speak_live(lines, ...)` owns playback. `render_to_sink(request, sink)` writes bounded output to a caller-owned `AudioSink` and leaves it open.

An `AudioSink` implements `write(audio, sample_rate)` and `close()`. For example, an application can implement this protocol to stream chunks into its own audio pipeline. Do not pass a sink to `json.dumps`; sinks are runtime resources, not result data.

## Projects and audiobooks

`app.projects.create(source, output=...)` creates a persistent project and returns a `ProjectRef`, the stable handle for later calls. `open()` loads a project, `find()` locates one if present, and `status()` returns typed stage status and next actions.

```python
from pathlib import Path

from readio.api import (
    CompositionOptions,
    ExportOptions,
    ProjectBuildRequest,
    Readio,
)

app = Readio()
project = app.projects.create(Path("article.md"), output=Path("article.readio"))
plan = app.projects.plan(project)
synthesis = app.projects.synthesize(project)
composition = app.projects.compose(project, CompositionOptions(target_lufs=-18.0))
exported = app.projects.export(project, ExportOptions(format="mp3"))

# Or incrementally build through a target stage. Existing reusable work is kept.
build = app.projects.build(project, ProjectBuildRequest(target="export"))
```

`ProjectBuildRequest` carries the stage target, selection, synthesis settings, composition options, and export options. `PreviewRequest` controls a selection and temporary output for a preview. Every lifecycle method returns a typed result, and mutating operations accept `on_event`.

`app.audiobooks.inspect(epub_path)` returns typed EPUB metadata and chapters. `create_project()` creates an ordinary Readio project. `create_project_result()` additionally returns the selected chapter numbers and scope IDs.

## Discovery and roles

`app.catalog.engines()`, `targets()`, `models()`, `voices()`, `lexicons()`, and `audio_formats()` expose typed discovery data. Listing methods such as `models_listing()` and `voices_listing()` wrap entries with `CatalogDiscovery` metadata, including source, cache fallback, offline, and refresh state. Pass `DiscoveryOptions(offline=True)` to prevent a network refresh.

```python
from readio.api import DiscoveryOptions, Readio, VoiceQuery

app = Readio()
listing = app.catalog.voices_listing(
    VoiceQuery(language="en-us"),
    discovery=DiscoveryOptions(offline=True),
)
for voice in listing.items:
    print(voice.selector, voice.id)
```

Catalogs also provide singular lookups and voice-selector resolution. `app.roles` lists and mutates global role bindings and inspects, binds, or unbinds project-local roles. Mutations persist; use a new `Readio` instance to observe changed configuration snapshots.

## SSMD, configuration, templates, and ingest

`app.ssmd.check()` validates a document, `analyze()` reports document/configured/runtime voice bindings, and `materialize_bindings()` writes an explicitly requested bound copy. `roundtrip_check()` provides strict authoring validation.

`app.configuration` loads, validates, and atomically saves `ReadioConfig`, sets dotted values, and manages language profiles/defaults. `app.templates` lists, reads, adds, removes, resets, seeds, and validates templates. `app.ingest.create()` creates an ingest file; `list()` and `directory` inspect the configured location. Read-only methods do not create storage directories.

## Diagnostics

`app.diagnostics.run()` returns a typed, local diagnostics report. `engines()` and `audio_formats()` expose the corresponding typed diagnostic collections. Diagnostics are read-only and do not authenticate with Spotify or inspect credential files.

## Engine extensions

`EngineAdapter` and its typed request/result/session contracts are public extension points in `readio.api` and `readio.api.extensions`. Implement the documented protocol and register an adapter with `register_engine(adapter)`. `registered_engines()` reports registrations. Prefer `engine_options` on `SynthesisRequest` for engine-specific settings. Engine identities and the public protocol, rather than private registry modules, are the compatibility boundary.

## Spotify integration

Spotify support is optional and is deliberately not imported by `import readio.api`. Import the integration explicitly:

```python
from pathlib import Path

from readio.api import Readio
from readio.api.integrations.spotify import SpotifyService, SpotifyUploadRequest

app = Readio()
spotify = SpotifyService(app)
result = spotify.upload(
    SpotifyUploadRequest(audio_path=Path("episode.mp3"), title="Episode")
)
```

The integration delegates to the separately installed `save-to-spotify` executable. `doctor()`, `shows()`, `upload()`, `publish()`, `publish_rendered()`, `status()`, and `set_timeline()` return typed values. Readio does not read Spotify credential files or perform authentication.

## Errors, events, and serialization

Expected public failures derive from `ReadioError` and expose stable `code`, `message`, optional `source_path`, and structured `details`. Catch the most specific API error when recovery depends on the failure category, or catch `ReadioError` at an application boundary.

Pass an `on_event` callback to long-running operations or to `Readio(on_event=...)`. Call-level events are delivered before the application-level handler. Events are immutable `ReadioEvent` values with machine-readable `kind`, `operation`, stage/progress fields, and structured details. Handler exceptions are not swallowed.

Public request and result objects are typed and immutable where applicable. Results intended for persistence or JSON output provide `to_dict()`. These mappings convert `Path` objects to strings and nested tuples to JSON arrays; for example, `json.dumps(result.to_dict())`. Do not serialize runtime resources such as an `AudioSink`.

## Stability and ownership

Symbols documented here and in `readio.api` or its documented submodules, public request/result fields, error codes, event fields/kinds, extension protocols, method semantics, and `to_dict()` keys are the supported compatibility surface. CLI helpers, private names, internal stage dictionaries, project layout, and cache sidecars are not stable API.

Input iterables and caller-provided audio sinks remain caller-owned and are not closed by Readio. Readio closes playback sinks and file sinks that it creates. Separate `Readio` instances can be used independently subject to the selected engine/runtime constraints. Mutations of the same project continue to use project locking; concurrent model sessions on one instance are not generally guaranteed.
