# Python API

`readio.api` is Readio's supported, synchronous Python application boundary. Import application objects and stable request/result types from `readio.api`. Modules outside that namespace are implementation details unless explicitly documented as an extension contract.

Importing `readio.api` does not import CLI adapters, optional engine runtimes, or Spotify integration code. Runtime integrations are loaded only by the operations that need them.
Both `readio.cli` and `readio.spotify_cli` are consumers of this application boundary. They may own argument parsing, prompts, terminal progress, human/JSON presentation, and process exit codes, but application and domain operations must go through `readio.api`. A regression test enforces that the CLIs do not import domain implementation modules directly.

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

`SynthesisRequest.speed` is a finite positive engine synthesis multiplier and is not also applied as composition rate. `voice_level` accepts `"off"` or `"calibrated"`; both settings are passed through typed planning and are part of the effective speech identity. PocketSynth currently supports only speed `1.0` and reports unsupported explicit values as a resolution error.

`render_live_to_file(lines, output, ...)` consumes the caller-owned iterable without closing it, resolves the output format/path, creates and closes its own file sink, and atomically commits the file. Its `RenderResult` includes `output_path` and `audio_format`. Live support is declared by each adapter's `capabilities().supports_live`; requesting live synthesis from an unsupported engine raises `InvalidRequestError` with code `speech.live_unsupported`.

For playback, call `app.speech.speak(request)`. It creates and closes the playback sink. For live text, `render_live(lines, sink, ...)` consumes but does not close the caller's iterable or sink. `speak_live(lines, ...)` owns playback. `render_to_sink(request, sink)` writes bounded output to a caller-owned `AudioSink` and leaves it open.

For PocketSynth, pass the registered bundle as `model`, choose a predefined `voice`, or supply a reference WAV with `voice_file`. Engine-specific generation options use `engine_options`:

```python
request = PlanRequest(
    operation="render",
    input=InputRequest(document=document_from_text("Hello.")),
    synthesis=SynthesisRequest(
        engine="pocket",
        model="BUNDLE_ID",
        voice_file=Path("reference.wav"),
        engine_options={"precision": "fp32", "temperature": 0.6},
    ),
    output=OutputRequest(requested_path=Path("pocket.wav")),
)
```

The resolver records the reference WAV's SHA-256 in the render target. It does not include a local filesystem path in the acoustic render identity.

An `AudioSink` implements `write(audio, sample_rate)` and `close()`. For example, an application can implement this protocol to stream chunks into its own audio pipeline. Do not pass a sink to `json.dumps`; sinks are runtime resources, not result data.

## Projects and audiobooks

`app.projects.create(source, output=...)` creates a persistent project and returns a `ProjectRef`, the stable handle for later calls. `open()` loads a project, `find()` locates one if present, and `status()` returns typed stage status and next actions.

```python
from pathlib import Path

from readio.api import (
    CompositionOptions,
    ExportOptions,
    AUDIOBOOK_EXPORT_FORMAT,
    SUPPORTED_AUDIOBOOK_FORMATS,
    AudiobookExportOptions,
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

`CompositionOptions.mastering` selects `spoken-word` by default (`-16 LUFS/-1 dBTP`); the other profiles are `spoken-word-dual-mono` (`-19/-1`), `broadcast-ebu` (`-23/-1`), `peak-safe` (no LUFS target, `-1 dBTP`), and `off` (no target or ceiling). `target_lufs` and `true_peak_ceiling_dbtp` override profile values when non-`None`; `None` inherits. For example, `CompositionOptions(mastering="broadcast-ebu", target_lufs=-21.0)` keeps the EBU true-peak ceiling and uses a `-21 LUFS` target.

Persist desired pipeline choices through `ProjectService` before running the stages. Settings writes use the project manifest's atomic update path, preserve schema-v2 compatibility and unrelated namespaces, and do not open a TTS session. `ProjectSettingsPatch` uses `UNSET` to leave a section unchanged, `None` to clear it, and a concrete immutable value to replace it:

```python
from pathlib import Path

from readio.api import (
    CompositionOptions,
    ExportOptions,
    ProjectSettings,
    ProjectSettingsPatch,
    ProjectSynthesisSettings,
    Readio,
    UNSET,
)

app = Readio()
project = app.projects.open(Path("article.readio"))
app.projects.configure(
    project,
    ProjectSettings(
        synthesis=ProjectSynthesisSettings(engine="piper", voice="en_US-amy-medium"),
        composition=CompositionOptions(target_lufs=-18.0),
        export=ExportOptions(format="mp3", output=Path("output/article.mp3")),
    ),
)
app.projects.update_settings(
    project, ProjectSettingsPatch(composition=None, export=UNSET)
)
```

`configure()` replaces supported sections while retaining `settings.ssmd` and unknown namespaces. `update_settings()` patches sections independently. Relative paths resolve from the project root. Saved synthesis settings are applied before global and engine defaults; explicit invocation requests override saved values without persisting those overrides. `force` and synthesis `refresh` remain invocation-only.

Requestless `synthesize()`, `compose()`, `export()`, and `build()` use saved settings. Status compares built provenance to desired settings: synthesis changes invalidate downstream stages, composition changes invalidate composition/output, and export changes invalidate output only. Caches and previous outputs remain available.

`ProjectCompositionResult.loudness` is a typed `LoudnessSummary` with before/after integrated LUFS, sample peak and true peak, requested/applied gain, target status, warning, and analysis/gain/post-gain metric timings. The mastering operation is transparent constant gain: `reduce_gain` may stop short of the LUFS target to honor the true-peak ceiling; this is not a true-peak limiter or ACX compliance check.

`app.audiobooks.inspect(epub_path)` returns typed EPUB metadata and chapters. `create_project()` creates an ordinary Readio project; `create_project_result()` additionally returns the selected chapter numbers and scope IDs. `app.audiobooks.export(project, AudiobookExportOptions(...))` writes M4B with chapters using the audiobook-specific API. `SUPPORTED_AUDIOBOOK_FORMATS` contains `m4b`; it is intentionally not in generic `SUPPORTED_AUDIO_FORMATS`.
`describe_project(project)` describes an existing audiobook after reopening it. The immutable `AudiobookProjectDescription` contains its `ProjectRef`, persisted source path, and persisted `AudiobookProjectChapter` values (chapter number, scope ID, title, and level). Readio loads the project and validates its kind; consumers do not need to inspect project files.
`create_project(..., settings=ProjectSettings(audiobook_export=...))` can save output, metadata, cover, and bitrate defaults at creation time. Omitted export options use those values; explicitly supplied non-`None` values override them for one invocation without mutating the manifest. `app.audiobooks.build(project)` runs the ordinary plan, synthesis, and composition services before M4B export. It respects the project's stored chapter scope and returns an `AudiobookExportResult`.

`app.projects.resolve_synthesis(project, request=None, *, voice_bindings=None, use_saved_settings=True)` returns a typed `SynthesisResolution` with effective engine, language, voice, model, model source, quality, speed, unit, pause mode, voice level, planning/provider settings, and pronunciation/resource policies: lexicons, G2P fallback, lexicon data policy, language-detection mode and languages, and experimental-option permission. It uses the same project synthesis resolver as `synthesize()`: fatal choices raise public API errors, and non-fatal resolver diagnostics are returned on the result.
`use_saved_settings=True` preserves saved project synthesis precedence for existing callers. Set it to `False` when a configuration editor or guided frontend is replacing saved engine, model, or voice choices. Fresh resolution ignores only persisted `ProjectSynthesisSettings`; request values, current Readio configuration and language defaults, and call-scoped `voice_bindings` still participate. It does not change sparse request semantics or make incomplete Piper/Pocket selections valid. Both modes remain non-rendering and non-mutating and perform no acoustic inference.

`lexicons` preserves three states: `None` means automatic or inherited behavior, an empty tuple means static lexicon layers were explicitly disabled, and a non-empty tuple lists selected layers. These fields expose resolved request/profile values; preflight does not materialize backend runtime defaults that were not resolved.

Resolution is read-only and intended for preflight, validation, and UI display. It does not render audio, open an engine session, activate a profile, or mutate project configuration, profile files, manifests, caches, audio, role bindings, or persistent synthesis/build state. It reports the same effective selection semantics used by actual project synthesis.

```python
from pathlib import Path
from readio.api import Readio, SynthesisRequest

app = Readio()
project = app.projects.open(Path("novel.readio"))
book = app.audiobooks.describe_project(project)
for chapter in book.chapters:
    print(chapter.number, chapter.title, chapter.scope_id)

request = SynthesisRequest(voice="af_heart", speed=1.0)
resolved = app.projects.resolve_synthesis(project, request)
print(resolved.engine, resolved.language, resolved.voice)
print(resolved.model, resolved.model_source, resolved.quality)
```

```python
book = app.projects.open(Path("novel.readio"))
m4b = app.audiobooks.export(
    book,
    AudiobookExportOptions(
        format=AUDIOBOOK_EXPORT_FORMAT,
        cover=Path("cover.jpg"),  # optional explicit JPEG/PNG
        bitrate="96k",
    ),
    on_event=handle_event,
)
```

Title and author default from the project's EPUB metadata and can be overridden. The audiobook M4B AAC bitrate defaults to 192k; generic M4A also defaults to 192k and generic Opus to 96k. These are Readio defaults, not a claim of TTSForge default parity. M4B identity includes the master/timeline, resolved metadata, explicit cover hash, and effective bitrate.

## Discovery and roles

`app.catalog.engines()`, `targets()`, `models()`, `voices()`, `lexicons()`, and `audio_formats()` expose typed discovery data. Listing methods such as `models_listing()` and `voices_listing()` wrap entries with `CatalogDiscovery` metadata, including source, cache fallback, offline, and refresh state. Pass `DiscoveryOptions(offline=True)` to prevent a network refresh.
Engine aliases `kokoro` -> `pykokoro` and `pipersynth` -> `piper` are canonical for engine, target, model, and voice catalog operations. `pocket` is a canonical engine ID. `app.catalog.normalize_engine()` exposes alias normalization. Lexicon queries are filtered through engine capabilities. The `voices list` CLI retains the convenience of treating a registered engine name supplied to `--model` as an engine filter when `--engine` is omitted.
Voice metadata distinguishes the lowercase base `language` (for example `en`), canonical descriptive `locale` (for example `en-US`), and stable `selector_language` namespace (for example `en` or `en_us`). A stable selector such as `en-pi-13` may identify an `en-US` voice without encoding the locale in its identity. Pocket generic language `en` matches a specific `en-US` query, while an explicit `en-GB` locale does not; generic metadata is not assigned unsupported regional specificity.

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

Catalogs also provide singular lookups and voice-selector resolution. `app.roles` lists and mutates global role bindings and inspects, binds, or unbinds project-local roles. New bindings resolve to the public `VoiceTarget` type, which contains canonical `engine` and `voice` values plus optional `target_id` and `selector`; `provider` is derived from the engine. `RoleBinding.target`, `ProjectRole.effective_target`, and `ProjectRole.project_target` expose these values. Project inspection's summary `provider` is `None` when effective roles use multiple providers. The JSON target objects include both engine and derived provider fields.

Bind mixed-engine roles through the same API used by the CLI:

```python
from pathlib import Path
from readio.api import Readio

app = Readio()
project = app.projects.open(Path("episode.readio"))
app.roles.bind_project(project, "host", "en_us-ko-4")
app.roles.bind_project(project, "guest", "en-pi-13")
inspection = app.roles.inspect_project(project)
for role in inspection.roles:
    target = role.effective_target
    if target is not None:
        print(role.role, target.engine, target.voice, target.provider)
```

`bind_global(role, voice, engine=...)` and `bind_project(project, role, voice, engine=...)` accept an explicit engine for raw voice IDs; stable selectors resolve their engine and retain target identity. Optional `provider` arguments remain for compatibility, engine/namespace validation, and inspection filtering; they do not select a project-wide route. A role-centric global target is stored in the top-level `[roles.<role>]` configuration. Legacy `[voices.<provider>.roles]` configuration and project `settings.ssmd.voice_bindings.<provider>.<role>` remain readable. Conflicting legacy definitions for the same global role or an unscoped project role produce explicit ambiguity diagnostics. The legacy project `settings.ssmd.voice_provider` scopes provider-keyed legacy inputs only. New project bindings use `settings.ssmd.role_bindings`, and no automatic migration command is provided. SSMD `voice_bindings` syntax is unchanged. Global configuration mutations persist; create a new `Readio` instance to use the saved configuration snapshot.

`unbind_project_result(project, role)` returns a `ProjectRoleMutationResult` with the removed `previous_project_binding`, resulting `project_binding`, newly effective target and voice, `origin`, and `status`. It avoids reopening the project just to inspect the state transition. The original `unbind_project()` remains available and continues returning `None` for API-v1 compatibility.

## SSMD, configuration, templates, and ingest

`app.ssmd.check()` and `analyze()` provide non-raising inspection of document and voice bindings. `app.ssmd.validate()` returns the same typed check result but raises the public `VoiceResolutionError` when voice references remain unresolved. This lets interactive clients inspect with `check()`, collect bindings, then call `validate()` without reconstructing domain errors. `materialize_bindings()` writes an explicitly requested bound copy; `roundtrip_check()` provides strict authoring validation.

`app.configuration` loads, validates, and atomically saves `ReadioConfig`, sets dotted values, and manages language profiles/defaults. `app.templates` lists, reads, adds, removes, resets, seeds, and validates templates. `app.ingest.create()` creates an ingest file; `list()` and `directory` inspect the configured location. Read-only methods do not create storage directories.

Use `LanguageProfilePatch` with `app.configuration.update_language_profile(language, patch)` for partial updates. Omitted fields use `UNSET` and remain unchanged. Explicit `None` clears nullable settings; for `lexicons`, `None` selects automatic lexicons while an empty tuple disables lexicon layers. `allow_experimental=False` is an explicit update. The method merges against the latest persisted profile and does not mutate the current `Readio.config` snapshot.

```python
from readio.api import LanguageProfilePatch, Readio

app = Readio()
app.configuration.update_language_profile(
    "en-us",
    LanguageProfilePatch(lexicons=None, allow_experimental=False),
)
```

## Diagnostics

`app.diagnostics.run()` returns a typed, local diagnostics report. `engines()` and `audio_formats()` expose the corresponding typed diagnostic collections. Diagnostics are read-only and do not authenticate with Spotify or inspect credential files.

## Engine extensions

`EngineAdapter`, `EngineSession`, `EngineCapabilities`, and `RequestMeasure` are public extension contracts in `readio.api` and `readio.api.extensions`. Implement the documented protocol and register an adapter with `register_engine(adapter)`. Each synthesis call is one exact Readio-shaped request: adapters must not select new text boundaries or invoke native convenience splitters. Readio uses optional measurement or typed too-long errors to fit an oversized request, subdivides exact text while respecting protected token and pronunciation ranges, then merges child audio and rebases timings. Adapters should declare only semantics they implement; unsupported explicit features fail with stable Readio errors before inference. Prefer `engine_options` on `SynthesisRequest` for engine-specific settings.

`registered_engines()` reports registered identifiers. Engine identities and the documented protocol, not private registry modules, are the compatibility boundary.

## Spotify integration

Spotify support is optional and is deliberately not imported by `import readio.api`. Import the integration explicitly:

```python
from pathlib import Path

from readio.api import Readio
from readio.api.integrations.spotify import SpotifyService, SpotifyUploadRequest

app = Readio()
spotify = SpotifyService(app)
result = spotify.upload(SpotifyUploadRequest(audio_path=Path("episode.mp3"), title="Episode"))
```

Live publishing uses `SpotifyService.publish_live()` so the integration owns audio rendering, temporary output, and upload as one operation:

```python
from readio.api import OutputRequest, Readio, SynthesisRequest
from readio.api.integrations.spotify import SpotifyLivePublishRequest, SpotifyService

spotify = SpotifyService(Readio())
result = spotify.publish_live(
    SpotifyLivePublishRequest(
        lines=iter(("Hello from a live source.\n",)),
        output=OutputRequest(requested_format="wav"),
        synthesis=SynthesisRequest(language="en-us"),
        title="Episode",
    )
)
```

When `requested_path` is omitted, live publishing uses a temporary audio file and removes it after upload or failure. An explicit `OutputRequest.requested_path` is retained. The input iterable stays caller-owned. Unsupported engine capability is reported as `speech.live_unsupported`; live publishing also exposes stable integration errors such as `spotify.live_output_invalid` and `spotify.audio_format_invalid`.

The integration delegates to the separately installed `save-to-spotify` executable. `doctor()`, `shows()`, `upload()`, `publish()`, `publish_live()`, `publish_rendered()`, `status()`, and `set_timeline()` return typed values. Readio does not read Spotify credential files or perform authentication.

## Errors, events, and serialization

Expected public failures derive from `ReadioError` and expose stable `code`, `message`, optional `source_path`, and structured `details`. Catch the most specific API error when recovery depends on the failure category, or catch `ReadioError` at an application boundary.

Speech planning failures raise `PlanNotExecutableError` with the resolved `plan` and typed `diagnostics`; output-related planning failures raise `PlannedOutputError`, which remains an `OutputError`. Both retain the serialized diagnostics in `details` for compatibility. CLI clients can render `error.plan` directly without repeating the planning operation.

Pass an `on_event` callback to long-running operations or to `Readio(on_event=...)`. Call-level events are delivered before the application-level handler. Events are immutable `ReadioEvent` values with machine-readable `kind`, `operation`, stage/progress fields, and structured details. Handler exceptions are not swallowed.

`EventKind`, `EventStage`, and `ProgressKind` are public `Literal` vocabularies. Event kinds distinguish operation/stage lifecycle from `progress`; stages identify plan, synthesis, composition, export, output, readiness, render, or upload. Progress subtypes identify phase, unit, segment, or item transitions. Consumers should branch on these fields rather than human-readable `message` text.

Public request and result objects are typed and immutable where applicable. Results intended for persistence or JSON output provide `to_dict()`. These mappings convert `Path` objects to strings and nested tuples to JSON arrays; for example, `json.dumps(result.to_dict())`. Do not serialize runtime resources such as an `AudioSink`.

## Stability and ownership

Symbols documented here and in `readio.api` or its documented submodules, public request/result fields, error codes, event fields/kinds, extension protocols, method semantics, and `to_dict()` keys are the supported compatibility surface. CLI helpers, private names, internal stage dictionaries, project layout, and cache sidecars are not stable API.

Input iterables and caller-provided audio sinks remain caller-owned and are not closed by Readio. Readio closes playback sinks and file sinks that it creates. Separate `Readio` instances can be used independently subject to the selected engine/runtime constraints. Mutations of the same project continue to use project locking; concurrent model sessions on one instance are not generally guaranteed.
