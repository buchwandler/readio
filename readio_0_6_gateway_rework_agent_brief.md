# Readio 0.6 Gateway Rework — Implementation Brief

**Target repository:** `readio`
**Target release:** **Readio 0.6.0**
**Version floor:** the current reconstructed Readio release history reaches **0.5.0**. Do not downgrade, reset, or reuse an older version number. This redesign is intentionally a breaking 0.x release and should land as **0.6.0**.

## 1. Executive directive

Readio must become the **single orchestration gateway** for the audio-generation stack:

```text
ssmdconvert
    ↓
utterplan
    ↓
RenderPlan
    ↓
voicerender
    ↓
audiocompose
    ↓
audioexport
```

Readio must **not** become another implementation of conversion, planning, synthesis, composition, or export. Those responsibilities remain in the specialist packages. Readio owns:

1. workflow intent,
2. lifecycle/orchestration,
3. state inspection and convergence,
4. temporary versus persistent workspace policy,
5. a unified Python API,
6. a workflow-oriented CLI,
7. engine/model/voice/profile/runtime management,
8. progress, diagnostics, and error normalization.

The CLI must no longer expose the individual media stages. A user should not have to remember:

```text
ssmdconvert bundle convert
utterplan bundle compile
utterplan bundle render
voicerender bundle render
audiocompose bundle compose
audioexport ...
```

Instead, the normal Readio user model is:

```text
text -> audio                         readio speak
file -> audio file                    readio render
file -> persistent SSMD bundle -> ... readio render --bundle ...
```

The specialist CLIs remain available for developers who deliberately want to debug or execute one stage.

---

# 2. What was reconstructed and reviewed

The supplied Codecrate snapshots were reconstructed with their generated unpackers in strict mode before inspection.

Observed latest recorded releases:

| Package      | Latest recorded release in supplied snapshot | Role                                                 |
| ------------ | -------------------------------------------: | ---------------------------------------------------- |
| Readio       |                                    **0.5.0** | orchestration/gateway                                |
| ssmdconvert  |                                    **0.2.4** | source -> SSMD bundle                                |
| utterplan    |                                   **0.6.17** | SSMD -> FlowPlan and FlowPlan -> RenderPlan          |
| voicerender  |                                    **0.2.8** | RenderPlan -> rendered speech                        |
| audiocompose |                                    **0.3.5** | rendered speech + timing semantics -> composed audio |
| audioexport  |                                    **0.1.5** | composed audio -> final formats                      |
| renderplan   |                                    **0.2.1** | shared planning/render contract                      |

The current Readio dependency declarations are older than several supplied package snapshots:

```text
utterplan>=0.6.6,<0.7
voicerender>=0.2.6,<0.3
audiocompose>=0.3.3,<0.4
ssmdconvert>=0.2.2,<0.3
```

AudioExport is currently not a required Readio runtime dependency, even though the desired `render` workflow must own the final file-output step.

There is also a concrete dependency conflict in the supplied package set:

```text
utterplan    -> renderplan>=0.2.1,<0.3
voicerender  -> renderplan>=0.2.1,<0.3
audiocompose -> renderplan>=0.1.2,<0.2
```

That conflict must be fixed before the integrated 0.6 stack can be considered healthy.

---

# 3. Interpretation of the handwritten architecture

The notes describe the right high-level pipeline:

```text
1. ssmdconvert bundle convert
2. utterplan bundle compile
3. voicerender profile create
4. utterplan bundle render        -> RenderPlan
5. voicerender bundle render      -> speech WAVs
6. audiocompose bundle compose    -> composed WAV
7. audioexport                    -> requested final format
```

One important adjustment is required:

**RenderProfile creation is not a mandatory data stage on every run.**

A RenderProfile is a control-plane input to RenderPlan projection.

Therefore:

- if a stored profile is supplied, load and use it;
- if `speak` is given direct engine/voice arguments, create an equivalent `RenderProfile` **in memory**;
- profile creation/refresh/editing remains available as a management command;
- do not recreate a persisted profile on every render.

The architectural data path should therefore be modeled as:

```text
source
  |
  v
SSMDConvert -> UtterPlan FlowPlan -> RenderPlan -> VoiceRender -> AudioCompose -> AudioExport
                                     ^
                                     |
                               RenderProfile
```

For `speak`, SSMDConvert and AudioExport disappear and the middle stages operate in memory:

```text
literal text
   |
   v
UtterPlan compile_document
   |
   v
FlowPlan
   |
   +---- RenderProfile (loaded or created in memory)
   |
   v
UtterPlan build_render_plan
   |
   v
VoiceRender preflight/replan/render in memory
   |
   v
AudioCompose in memory
   |
   v
AudioBuffer
   |
   +--> playback
```

---

# 4. Current Readio problems

## 4.1 The public API exposes implementation stages instead of a gateway

The current `Readio` service publicly exposes methods such as:

```text
create_workspace
plan
render_plan
render
compose
export
run
status
render_intent
```

and `readio.api` exports stage-specific request types such as:

```text
PlanRequest
RenderPlanRequest
VoiceRenderRequest
ComposeRequest
ExportRequest
RunRequest
PipelineResult
```

This gives Readio two competing personalities:

1. high-level workflow service,
2. manual stage runner.

That is the wrong boundary for 0.6.

The specialist packages already own the stage APIs. Readio should not mirror them.

## 4.2 `Readio.run()` blindly executes all stages

The current `_run_pipeline()` does:

```text
ensure_workspace
plan
render_plan
render
compose
export
```

unconditionally.

It does not first determine which selected work already exists and is valid.

That makes incremental operation fragile and pushes `force` decisions into the user interface.

## 4.3 `readio speak` bypasses the semantic pipeline

Current `readio/speech.py` resolves a VoiceRender engine and directly executes:

```python
with voicerender.open_session(selection) as session:
    rendered = voicerender.synthesize(session, request)
```

That bypasses:

- UtterPlan text preparation and segmentation,
- FlowPlan semantics,
- RenderPlan construction,
- RenderPlan preflight/replanning,
- RenderPlan voice-role semantics,
- AudioCompose pause/emphasis/timeline behavior.

Therefore current `speak` is not a small version of the real Readio pipeline. It is a separate TTS shortcut.

This must change.

## 4.4 Persistent bundles are initially populated with the selected chapters only

Current `render_service.render_intent()` creates a new bundle with:

```python
CreateWorkspaceRequest(
    ...
    chapters=resolved.chapters,
)
```

This is the central problem for the requested workflow.

Example:

```text
readio render novel.epub --bundle novel.ssmd-bundle --chapters 1 ...
```

currently allows the first conversion to be scoped to chapter 1.

Later:

```text
readio render novel.ssmd-bundle --chapters 2 ...
```

cannot work from that bundle if chapter 2 was never materialized by SSMDConvert.

For a **persistent book bundle**, chapter selection must not control initial source materialization.

Instead:

```text
persistent bundle creation:
    SSMDConvert chapters = all

execution selection:
    UtterPlan/RenderPlan/VoiceRender/AudioCompose/AudioExport chapters = user selection
```

This is the key to:

```text
day 1 -> chapter 1
day 2 -> chapter 2
```

without redoing chapter 1 and without reconverting the book.

## 4.5 Current status is architecturally correct but incomplete

`readio/status.py` correctly tries to treat specialist packages as owners of stage truth.

However, the reconstructed package set does not expose all of the read-only inspector functions Readio expects.

Readio currently wants equivalents of:

```text
utterplan.inspect_bundle_compile
voicerender variant/render inspection
audiocompose.inspect_bundle_composition
audioexport.inspect_bundle_export
```

but only part of this contract is currently available.

The result is status states such as `unknown`/`unavailable`, which are not strong enough to drive automatic convergence.

## 4.6 Current CLI exposes the exact manual lifecycle that Readio should hide

The current CLI has a `bundle` group with individual commands for:

```text
convert
compile
render-plan
render
compose
export
run
status
```

This duplicates the specialist CLIs and contradicts the desired Readio mental model.

These commands should be removed from the Readio 0.6 CLI.

## 4.7 Intent resolution implicitly creates a persistent bundle path

When a source is not already a bundle and no bundle is explicitly supplied, the current resolver falls back to a default `.ssmd-bundle` path.

That means a normal render implicitly creates durable intermediate state.

For 0.6 the default must be the opposite:

```text
no --bundle      -> temporary workspace
explicit --bundle -> persistent workspace
SOURCE is bundle  -> persistent existing workspace
```

No hidden `.readio` directory and no implicit persistent SSMD bundle.

---

# 5. Readio 0.6 product contract

## 5.1 Stable workflow API

The stable media API should be centered on only three operations:

```python
gateway = Readio()

gateway.speak(...)
gateway.render(...)
gateway.inspect(...)
```

`inspect()` is the non-mutating planning/status operation used by `--dry-run` and status tooling.

Everything else related to media stages becomes internal orchestration.

## 5.2 Stable control API

Readio should remain the unified control surface for VoiceRender-owned management functions.

Recommended façade shape:

```python
gateway.engines.list()
gateway.engines.inspect(...)
gateway.engines.options(...)

gateway.models.list(...)
gateway.models.inspect(...)
gateway.models.install(...)
gateway.models.remove(...)
gateway.models.verify(...)

gateway.voices.list(...)
gateway.voices.inspect(...)
gateway.voices.clone(...)
gateway.voices.sources.create(...)
gateway.voices.sources.inspect(...)
gateway.voices.sources.verify(...)

gateway.profiles.create(...)
gateway.profiles.load(...)
gateway.profiles.validate(...)
gateway.profiles.refresh(...)
gateway.profiles.roles.list(...)
gateway.profiles.roles.set(...)
gateway.profiles.roles.remove(...)

gateway.runtime.status()
gateway.runtime.list()
gateway.runtime.use(...)
gateway.runtime.reset()
```

These can initially be thin wrapper objects around the already-existing Readio management modules. Do not rewrite VoiceRender functionality.

The important property is that a caller can keep one `Readio` object and use one namespace.

## 5.3 What Readio must not expose as primary API

Do not make these first-class Readio 0.6 workflow operations:

```text
gateway.convert(...)
gateway.compile(...)
gateway.project_renderplan(...)
gateway.render_segments(...)
gateway.compose(...)
gateway.export(...)
```

Those are specialist-package responsibilities.

If an advanced developer needs a single stage, they should import:

```python
import ssmdconvert
import utterplan
import voicerender
import audiocompose
import audioexport
```

directly.

This keeps Readio small and stops its public surface from tracking every option of every package.

---

# 6. Target architecture

Use a four-layer architecture.

```text
┌─────────────────────────────────────────────────────────────┐
│ CLI                                                         │
│ speak | render | status/inspect | intent | control commands │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              v
┌─────────────────────────────────────────────────────────────┐
│ Public Readio API                                           │
│ Readio.speak / Readio.render / Readio.inspect               │
│ engines / models / voices / profiles / runtime              │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              v
┌─────────────────────────────────────────────────────────────┐
│ Workflow planner + convergence executor                     │
│ - resolve intent                                            │
│ - choose temp/persistent workspace                          │
│ - inspect state                                             │
│ - calculate minimal actions                                 │
│ - execute / re-inspect                                      │
│ - normalize progress/errors                                 │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              v
┌─────────────────────────────────────────────────────────────┐
│ Thin specialist adapters                                    │
│ SSMDConvert | UtterPlan | VoiceRender | AudioCompose | Export│
└─────────────────────────────┬───────────────────────────────┘
                              │
                              v
┌─────────────────────────────────────────────────────────────┐
│ Specialist public APIs and their artifact formats           │
└─────────────────────────────────────────────────────────────┘
```

## 6.1 Hard boundary rules

The coding agent should enforce these rules:

1. Readio imports only **public package-root APIs** or explicitly documented public submodules.
2. Readio does not parse or rewrite specialist package artifacts to infer state.
3. Readio does not duplicate SSMD parsing, FlowPlan logic, RenderPlan validation, renderer cache logic, composition logic, or export verification.
4. Specialist packages own their files and their stale/reuse rules.
5. Readio may normalize specialist status objects into a Readio workflow state.
6. Readio owns only its own intent/config schema.
7. Temporary files belong to a run-specific temporary workspace and disappear at the end.
8. Persistent generated state lives in the explicit `.ssmd-bundle`; never in `.readio`.

---

# 7. Two execution modes for `render`

## 7.1 Temporary mode — default

Command:

```bash
readio render article.docx \
  --profile narrator.renderprofile.toml \
  --output article.mp3
```

Semantics:

```text
source file
   |
   v
system temporary directory
   |
   +-- SSMD bundle
   +-- UtterPlan artifacts
   +-- RenderPlan artifacts
   +-- VoiceRender artifacts
   +-- AudioCompose artifacts
   |
   v
AudioExport -> requested final output outside temporary directory
   |
   v
temporary directory deleted
```

Properties:

- no durable `.ssmd-bundle` unless requested;
- no `.readio` directory;
- final output is atomic and survives cleanup;
- on failure, temporary workspace is cleaned;
- useful for one-shot files.

In temporary mode it is acceptable for initial SSMD conversion to honor a chapter selection because reuse is not expected after the run.

## 7.2 Persistent bundle mode — explicit

Command:

```bash
readio render novel.epub \
  --bundle novel.ssmd-bundle \
  --profile narrator.renderprofile.toml \
  --chapters 1 \
  --output chapter-01.m4b
```

On first creation:

```text
SSMDConvert source scope = ALL chapters
downstream execution scope = chapter 1
```

The persistent bundle therefore contains the complete source inventory even though only chapter 1 is processed further.

Later:

```bash
readio render novel.ssmd-bundle \
  --profile narrator.renderprofile.toml \
  --chapters 2 \
  --output chapter-02.m4b
```

must:

```text
conversion     reuse
compile        chapter 2 only if missing/stale
render plan    add/project chapter 2 only
voice render   chapter 2 missing speech only
compose        chapter 2 only
export         selected output
```

Chapter 1 must not be recompiled or overwritten.

The supplied UtterPlan tests already demonstrate incremental extension of a book RenderPlan set: one chapter is projected, a second chapter is projected later, and the same variant set contains both. Readio must build on that capability rather than defeating it by recreating workspaces.

---

# 8. Workspace resolution contract

Create a single workspace policy function.

Suggested internal type:

```python
@dataclass(frozen=True)
class WorkspacePolicy:
    mode: Literal["temporary", "persistent"]
    root: Path
    source_is_bundle: bool
    newly_created: bool
    cleanup: bool
```

Resolution rules, in this exact order:

```text
1. SOURCE is an existing .ssmd-bundle
   -> persistent
   -> root = SOURCE
   -> never reconvert automatically

2. --bundle PATH / intent workspace.bundle is explicitly supplied
   -> persistent
   -> root = PATH
   -> create if absent
   -> if creating a book: SSMDConvert chapters="all"

3. otherwise
   -> temporary
   -> create TemporaryDirectory
   -> SSMDConvert into temp workspace
   -> cleanup after success or failure
```

Do not infer persistent mode from a default filename.

## 8.1 Existing source file + existing persistent bundle

Before reusing an explicitly named persistent bundle, validate source identity/provenance through SSMDConvert-owned APIs.

Do not silently assume:

```text
book.epub + some-existing.ssmd-bundle
```

belong together.

If the source changed relative to the existing bundle:

- report `stale_source`;
- do not silently destroy/reconvert the bundle;
- require an explicit rebuild-from-convert operation.

The exact provenance check should come from SSMDConvert rather than Readio comparing arbitrary files itself.

---

# 9. Convergence instead of "run every stage"

The core 0.6 change should be a convergence engine.

A workflow first calculates the desired state, then asks each specialist package about current state, then performs only the necessary actions.

## 9.1 Normalized Readio stage states

Readio may normalize package-specific reports to:

```python
StageState = Literal[
    "not_started",
    "partial",
    "complete",
    "stale",
    "blocked",
    "error",
]
```

Avoid `unknown` as an executable state.

`unknown` is acceptable for display when an optional inspector is unavailable during development, but the automatic convergence engine must **fail closed** rather than deleting or forcing artifacts when it cannot prove what is safe.

## 9.2 Recommended specialist inspector shape

Do not require every package to depend on a shared Readio class.

Each package can expose its own dataclass, but it should make these concepts available:

```python
@dataclass(frozen=True)
class ExampleInspection:
    state: Literal["not_started", "partial", "complete", "stale", "error"]
    selected: tuple[str, ...]
    complete: tuple[str, ...]
    missing: tuple[str, ...]
    stale: tuple[str, ...]
    reason: str | None
    force_required: bool
```

Readio adapters normalize these reports.

## 9.3 Convergence rules

For every stage:

```text
complete
  -> reuse
  -> no write

not_started
  -> execute only selected missing work

partial
  -> execute missing work only

stale
  -> rebuild only the stale subset
  -> pass force/reset only if owning package explicitly reports this as safe

error/corrupt/unknown
  -> fail closed
  -> do not automatically delete user data
```

After a mutating action:

1. re-inspect;
2. require the desired selection to be complete;
3. only then move to the next stage.

## 9.4 No filename guessing in Readio

Do not implement logic such as:

```python
if (bundle / "utterplan/chapter-0002.utterplan.toml").exists():
    ...
```

or:

```python
if output.wav.exists():
    assume_complete = True
```

Existence is not identity, validity, or freshness.

The owning package must answer this.

---

# 10. Workflow planning model

Add a read-only `WorkflowPlan` generated by `Readio.inspect()` and used internally by `Readio.render()`.

Suggested types:

```python
WorkflowAction = Literal["reuse", "execute", "replace", "blocked"]

@dataclass(frozen=True)
class WorkflowStep:
    stage: Literal[
        "convert",
        "compile",
        "render_plan",
        "render",
        "compose",
        "export",
    ]
    package: str
    action: WorkflowAction
    selector: str | None
    reason: str
    force: bool = False

@dataclass(frozen=True)
class WorkflowPlan:
    source: Path
    workspace: Path | None
    workspace_mode: Literal["temporary", "persistent"]
    variant: str | None
    output: Path | None
    steps: tuple[WorkflowStep, ...]
```

`readio render --dry-run` should display this plan.

A dry run must never create:

- a bundle,
- a RenderPlan,
- renderer files,
- composition files,
- final output.

For a source that has not yet been converted and therefore cannot be fully inspected, the plan may state that downstream actions are provisionally required.

---

# 11. `speak`: the in-memory semantic pipeline

## 11.1 Required behavior

`Readio.speak()` must use the same semantic core as full rendering.

Required chain:

```text
literal text
  -> utterplan.compile_document(input_format="plain")
  -> resolve/create RenderProfile
  -> utterplan.build_render_plan(...)
  -> voicerender.preflight_render_plan(..., persist=False)
  -> if replan requested:
         utterplan.build_render_plan(previous_plan=..., preflight=...)
         repeat preflight
  -> voicerender.render_renderplan_memory(...)
  -> audiocompose.compose_renderplan_memory(...)
  -> Readio AudioBuffer
```

No pipeline artifact files are created.

Engine-managed model caches and voice assets are not Readio pipeline artifacts and do not count as a violation of this rule.

## 11.2 Profile handling

Two valid forms:

### Stored profile

```python
SpeakRequest(
    text="Hello.",
    profile=Path("narrator.renderprofile.toml"),
)
```

Load with VoiceRender and use the selected/default role.

### Direct voice arguments

```python
SpeakRequest(
    text="Hello.",
    engine="kokoro",
    target="v1.0",
    voice="af_heart",
    language="en-US",
)
```

Use `voicerender.create_render_profile(...)`, which already returns an in-memory `RenderProfile`.

Do not save it unless the user separately invokes a profile-management operation.

## 11.3 Replanning loop

VoiceRender preflight can return `replan`.

Implement a bounded loop:

```text
plan
preflight
  |
  +-- ok     -> render
  +-- reject -> fail
  +-- replan -> UtterPlan build_render_plan(previous_plan, preflight)
                -> preflight again
```

Safety requirements:

- maximum replan count, for example 8;
- detect repeated RenderPlan identity;
- if a replan does not change the plan, raise a useful orchestration error;
- propagate the failing segment identity and measurement details.

## 11.4 Result type

Suggested public result:

```python
@dataclass(frozen=True)
class AudioBuffer:
    audio: np.ndarray
    sample_rate: int

    @property
    def channels(self) -> int: ...

@dataclass(frozen=True)
class SpeakResult:
    audio: AudioBuffer
    profile_id: str
    warnings: tuple[str, ...] = ()
    elapsed_seconds: float | None = None
```

Do not expose VoiceRender's renderer-specific result as the primary public return type.

Optional debug metadata may be available behind an explicit diagnostics field, but the ordinary API should remain stable across specialist package changes.

## 11.5 `speak` CLI

Target:

```bash
readio speak "Hello, how are you?" \
  --voice kokoro:v1.0/af_heart \
  --lang en-US
```

or:

```bash
readio speak "Hello, how are you?" \
  --profile narrator.renderprofile.toml
```

Default behavior: compose the complete in-memory result and play it.

Useful flags:

```text
--no-play
--profile
--role
--voice
--engine
--target
--lang
--speaker
--engine-option KEY=VALUE
--offline
--refresh
--json
```

### Remove `speak --output`

Recommended for the clean 0.6 contract.

`speak` means:

```text
text in -> audio buffer / sound out
```

`render` means:

```text
file in -> audio file out
```

Keeping `speak --output` blurs this contract and reintroduces an ad-hoc WAV writer outside AudioExport.

If backward compatibility is intentionally retained, `--output` must be documented as a deprecated transitional exception and removed in a later release. For a clean 0.6 break, delete it now.

---

# 12. `render`: file-to-file convergence workflow

## 12.1 Required command behavior

Examples:

```bash
readio render article.md \
  --profile narrator.renderprofile.toml \
  -o article.mp3
```

Temporary workspace by default.

```bash
readio render novel.epub \
  --bundle novel.ssmd-bundle \
  --profile narrator.renderprofile.toml \
  --chapters 1 \
  -o chapter-01.m4b
```

Persistent bundle creation.

```bash
readio render novel.ssmd-bundle \
  --profile narrator.renderprofile.toml \
  --chapters 2 \
  -o chapter-02.m4b
```

Persistent bundle reuse.

```bash
readio render --config novel.readio.toml
```

Config-driven workflow.

## 12.2 Stage execution

For a new temporary workspace:

```text
convert -> compile -> render plan -> render -> compose -> export
```

For an existing persistent bundle:

```text
inspect all selected stages
    |
    v
execute only the missing/stale safe subsets
    |
    v
export requested output
```

## 12.3 Profile/variant semantics

A RenderProfile identity is part of the desired state.

Rules:

- same profile + same variant identity -> extend/reuse existing variant;
- changed profile -> new profile identity;
- do not overwrite an existing variant produced by a different profile;
- explicit variant aliases may be reused only if specialist validation says the profile identity matches;
- keep previous variants intact.

The supplied UtterPlan implementation already has safeguards around profile/snapshot mismatch. Readio should call those public APIs rather than weaken them.

## 12.4 Output collision

Final user output is special because it is outside package-owned intermediate state.

Default:

```text
existing output -> fail
```

Allow explicit replacement:

```bash
--replace-output
```

Do not overload stage-level `force` with final-output replacement.

---

# 13. Rebuild controls

The current `--force-stage` mechanism exposes too much stage machinery.

Replace it with one workflow-oriented control:

```bash
--rebuild-from {convert,compile,render-plan,render,compose,export}
```

Meaning:

```text
invalidate/re-execute the named stage and every downstream derived stage
for the selected scope, using package-owned safe replacement operations.
```

Examples:

```bash
readio render ... --rebuild-from compile
readio render ... --rebuild-from render
```

Rules:

- `convert` is invalid if SOURCE itself is an existing bundle;
- rebuilding from `convert` on an explicit source + persistent bundle requires explicit confirmation semantics via the flag itself;
- no hidden broad deletion;
- package inspectors decide exactly which artifacts are stale or replaceable;
- final output still obeys `--replace-output` unless `--rebuild-from export` is explicitly defined to imply it. Prefer keeping these two concerns separate.

An alias `--reconvert` can be retained temporarily as:

```text
--reconvert == --rebuild-from convert
```

but the canonical 0.6 concept should be one rebuild boundary.

---

# 14. Intent/config TOML

Keep the user-reviewable intent file. It is valuable because users should not have to memorize a large TOML schema.

The existing:

```text
readio intent document create ...
readio intent book create ...
```

concept should remain.

However, workspace semantics need a clean 0.6 definition.

Recommended new schema:

```toml
schema = "readio.render.v2"
kind = "book"

[input]
source = "novel.epub"
chapters = "1"
language = "en-US"

[workspace]
mode = "bundle"
bundle = "novel.ssmd-bundle"

[voice]
profile = "narrator.renderprofile.toml"
variant = "narration"

[planning]
text_preparation = "auto"
diagnostics = "errors"

[render]
speech = "all"
emphasis_policy = "defer-to-compose"

[compose]
emphasis_policy = "segment_fallback"

[output]
path = "chapter-01.m4b"
format = "m4b"
```

Temporary version:

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

[output]
path = "article.mp3"
format = "mp3"
```

## 14.1 Do not expose persistent population policy as a normal user option

Do **not** make users learn:

```toml
populate = "all"
```

Persistent book population should simply be a Readio invariant:

```text
persistent book bundle = complete source inventory
```

`input.chapters` controls downstream execution, not what the persistent source workspace contains.

## 14.2 Override precedence

Use one documented precedence order:

```text
CLI explicit override
    >
intent TOML
    >
Readio defaults
```

Do not make package-specific environment variables silently override intent values unless the package itself documents that behavior and Readio surfaces it in diagnostics.

---

# 15. CLI surface for 0.6

The Readio CLI should be small and intention-oriented.

Recommended top-level structure:

```text
readio speak
readio render
readio status
readio intent

readio engines ...
readio models ...
readio voices ...
readio voice ...
readio profile ...
readio runtime ...

readio doctor
```

## 15.1 Remove these Readio commands

Delete the Readio `bundle` stage-runner group:

```text
readio bundle convert
readio bundle compile
readio bundle render-plan
readio bundle render
readio bundle compose
readio bundle export
readio bundle run
```

If bundle inspection is useful, put it under:

```text
readio status BUNDLE
```

or:

```text
readio render BUNDLE ... --dry-run
```

Do not keep a mostly empty `bundle` namespace just for compatibility unless there is a concrete user-facing need.

## 15.2 Keep specialist CLIs for specialist workflows

Documentation should explicitly say:

```text
Need to debug SSMD conversion only?     use ssmdconvert
Need to compile one FlowPlan manually?  use utterplan
Need renderer-level inspection?         use voicerender
Need composition diagnostics?           use audiocompose
Need export-only experiments?           use audioexport
```

Readio is the workflow gateway, not a replacement shell for every specialist command.

---

# 16. Public Python API proposal

## 16.1 Workflow requests

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Any

@dataclass(frozen=True)
class SpeakRequest:
    text: str
    profile: Path | None = None
    role: str | None = None
    voice: str | None = None
    engine: str | None = None
    target: str | None = None
    language: str | None = None
    speaker: str | int | None = None
    engine_options: Mapping[str, Any] = field(default_factory=dict)
    offline: bool = False
    refresh: bool = False

@dataclass(frozen=True)
class RenderRequest:
    source: Path
    profile: Path
    chapters: str = "all"
    language: str | None = None

    # None means temporary unless source itself is a bundle.
    bundle: Path | None = None

    variant: str | None = None

    output: Path | None = None
    format: str | None = None
    bitrate: str | int | None = None
    sample_rate: int | None = None
    channels: int | None = None

    rebuild_from: Literal[
        "convert",
        "compile",
        "render_plan",
        "render",
        "compose",
        "export",
    ] | None = None

    replace_output: bool = False
```

Keep detailed planning/render/compose settings in nested immutable option objects rather than allowing `RenderRequest` to grow without limit.

For example:

```python
@dataclass(frozen=True)
class PlanningOptions: ...

@dataclass(frozen=True)
class VoiceRenderOptions: ...

@dataclass(frozen=True)
class ComposeOptions: ...

@dataclass(frozen=True)
class ExportOptions: ...
```

Then:

```python
RenderRequest(
    ...,
    planning=PlanningOptions(...),
    voice_render=VoiceRenderOptions(...),
    compose=ComposeOptions(...),
    export=ExportOptions(...),
)
```

## 16.2 Results

```python
@dataclass(frozen=True)
class RenderResult:
    output: Path
    workspace: Path | None
    workspace_mode: Literal["temporary", "persistent"]
    variant: str
    executed_stages: tuple[str, ...]
    reused_stages: tuple[str, ...]
    warnings: tuple[str, ...]
```

For temporary mode, returning the deleted temporary path is not useful. Prefer:

```python
workspace=None
workspace_mode="temporary"
```

after cleanup.

## 16.3 Canonical use

```python
gateway = Readio()

result = gateway.render(
    RenderRequest(
        source=Path("novel.epub"),
        profile=Path("narrator.renderprofile.toml"),
        bundle=Path("novel.ssmd-bundle"),
        chapters="1",
        output=Path("chapter-01.m4b"),
        format="m4b",
    )
)
```

Then later:

```python
result = gateway.render(
    RenderRequest(
        source=Path("novel.ssmd-bundle"),
        profile=Path("narrator.renderprofile.toml"),
        chapters="2",
        output=Path("chapter-02.m4b"),
        format="m4b",
    )
)
```

## 16.4 Convenience wrappers

Optional:

```python
readio.speak(...)
readio.render(...)
```

may be retained as thin wrappers that instantiate/delegate to the same `Readio` façade.

There must be only **one implementation path**.

---

# 17. Specialist package work required before Readio 0.6

The stack is close, but Readio cannot implement the clean gateway correctly by guessing around missing package APIs.

Implement package changes in the following order.

---

# 18. 01 — AudioCompose 0.3.6: RenderPlan compatibility + in-memory composition + inspection

This is the first required change because the current dependency range conflicts with UtterPlan and VoiceRender.

## 18.1 Dependency correction

Change:

```text
renderplan>=0.1.2,<0.2
```

to:

```text
renderplan>=0.2.1,<0.3
```

Then run the full AudioCompose suite against RenderPlan 0.2.1.

Do not merely widen the requirement without verifying schema/identity behavior.

## 18.2 Add an in-memory RenderPlan composition API

Current `compose_renderplan(...)` expects a plan path, render directory, and output path.

`speak` needs the same semantic composition without persisted renderer WAVs.

Add a public API conceptually like:

```python
@dataclass(frozen=True)
class RenderedAudioBuffer:
    audio: np.ndarray
    sample_rate: int
    word_timings: tuple[...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class RenderPlanMemoryComposeResult:
    audio: np.ndarray
    sample_rate: int
    composition: CompositionResult
    warnings: tuple[str, ...] = ()

def compose_renderplan_memory(
    plan: RenderPlan,
    rendered: Mapping[tuple[int, str], RenderedAudioBuffer],
    *,
    media_root: str | Path | None = None,
    output_policy: OutputPolicy | None = None,
    emphasis_policy: Literal["segment_fallback", "strict"] = "segment_fallback",
    on_progress: CompositionProgressCallback | None = None,
) -> RenderPlanMemoryComposeResult:
    ...
```

The exact public dataclass names may differ.

Critical requirements:

- do not import VoiceRender-specific concrete result classes;
- key speech by stable RenderPlan occurrence identity, preferably `(segment_number, segment_id)`;
- reuse the existing RenderPlan -> AudioJob materialization logic;
- reuse `Composer.compose(...)`;
- preserve pauses;
- preserve emphasis fallback behavior;
- preserve external audio/media handling;
- preserve anchors/spans/timing metadata;
- do not create a WAV, AudioJob file, or report unless explicitly requested by a different persisted API.

Do not create a second composition implementation for memory mode.

## 18.3 Add read-only bundle composition inspection

Add:

```python
inspect_bundle_composition(
    bundle,
    *,
    variant,
    chapters="all",
    ...
)
```

It must:

- be read-only;
- validate composition identity;
- report selected complete/missing/stale chapters/items;
- distinguish partial from stale;
- expose why a result is stale;
- not infer complete solely from output-file existence.

## 18.4 AudioCompose acceptance tests

Must include:

1. RenderPlan 0.2.1 compatibility.
2. Memory composition output semantically matches persisted composition for the same synthetic rendered buffers.
3. Pause timing is identical.
4. Emphasis behavior is identical.
5. External audio semantics are identical.
6. Inspector does not change any file.
7. Corrupt/stale composition is reported as stale/error, not complete.
8. Existing persisted reuse tests still pass.

**Proposed release:** `audiocompose 0.3.6`.

---

# 19. 02 — UtterPlan 0.6.18: read-only compile and projection inspection

UtterPlan already has the main functionality Readio needs:

```text
compile_document
compile_bundle
build_render_plan
render_bundle
replan_bundle
select_bundle_items
```

and its tests demonstrate incremental book projection.

Do not rewrite these operations.

## 19.1 Add compile inspection

Add a public read-only function such as:

```python
inspect_bundle_compile(
    bundle,
    *,
    chapters="all",
    language=None,
    planner_config=None,
) -> BundleCompileInspection:
    ...
```

It should report per selected bundle item:

```text
not_started
complete
stale
error
```

and aggregate:

```text
not_started
partial
complete
stale
error
```

The package owns enough FlowPlan provenance to compare source identity rather than Readio checking mtimes or filenames.

Expose the smallest selector that Readio can use to execute only missing/stale work.

Example:

```text
requested: 1-3
chapter 1: complete
chapter 2: complete
chapter 3: missing

recommended selector: "3"
```

## 19.2 Add RenderPlan projection inspection

Projection is owned by UtterPlan, even though VoiceRender can discover variants.

Add a public function such as:

```python
inspect_bundle_projection(
    bundle,
    *,
    profile,
    chapters="all",
    variant=None,
) -> BundleProjectionInspection:
    ...
```

Report:

- resolved variant;
- RenderProfile identity;
- selected chapters;
- chapters already in the RenderPlan set;
- missing chapters;
- stale chapters;
- profile/snapshot mismatch;
- whether `force=True` is required for the recommended subset.

This lets Readio safely decide:

```text
chapter 1 already projected
chapter 2 not projected
=> call utterplan.render_bundle(..., chapters="2")
```

rather than retrying chapter 1 and receiving the existing-output protection error.

## 19.3 Preserve incremental semantics

Do not change the useful existing behavior:

- partial book compile can operate on a subset;
- later projection can extend the same RenderPlan set;
- a different profile produces/is validated as a different profile identity;
- protected existing outputs require explicit replacement when targeted directly.

## 19.4 UtterPlan acceptance tests

Add tests for:

1. fresh bundle -> selected chapters `not_started`;
2. chapter 1 compiled -> chapter 1 `complete`, chapter 2 `not_started`;
3. source chapter changed -> only that chapter `stale`;
4. same planner config -> valid reuse;
5. changed planner-relevant config -> appropriate stale state;
6. profile A projection -> complete for A;
7. profile B does not masquerade as A;
8. project chapter 1, inspect chapter 2, project chapter 2, verify chapter 1 unchanged;
9. inspectors are read-only by tree/hash snapshot.

**Proposed release:** `utterplan 0.6.18`.

---

# 20. 03 — VoiceRender 0.2.9: in-memory RenderPlan execution + selection-aware inspection

VoiceRender currently has the right RenderPlan renderer, but its RenderPlan execution path is file-oriented.

## 20.1 Make preflight persistence optional

Current:

```python
preflight_render_plan(plan, output, ...)
```

always persists the report.

Change compatibly to:

```python
preflight_render_plan(
    plan,
    output: str | Path | None = None,
    ...
) -> PreflightReport
```

Semantics:

```text
output is Path -> current behavior, persist report
output is None -> return report only, write nothing
```

Do not create a separate measurement implementation.

## 20.2 Add in-memory RenderPlan rendering

Add:

```python
@dataclass(frozen=True)
class RenderedOccurrence:
    number: int
    segment_id: str
    speech: RenderedSpeech

@dataclass(frozen=True)
class RenderPlanMemoryResult:
    render_plan_id: str
    rendered: tuple[RenderedOccurrence, ...]
    warnings: tuple[str, ...] = ()

def render_renderplan_memory(
    plan: RenderPlan,
    *,
    preflight: PreflightReport,
    speech_numbers: Collection[int] | None = None,
    emphasis_policy=...,
    max_open_sessions: int = 1,
    on_event: RenderEventCallback | None = None,
) -> RenderPlanMemoryResult:
    ...
```

Requirements:

- same `speech_request_conversion_from_renderplan`;
- same binding resolution;
- same engine session pooling;
- same capability checks;
- same selected-speech behavior;
- same preflight validation;
- same emphasis defer/reject semantics;
- no numbered WAV files;
- no render manifest on disk;
- no diagnostics file on disk.

Refactor the persisted renderer and memory renderer to share one execution core.

Do not duplicate renderer logic.

## 20.3 Add selection-aware persisted render inspection

`list_bundle_variants()` is useful for discovery but is not enough for a convergence planner scoped to chapters/speech.

Add a public function such as:

```python
inspect_bundle_render(
    bundle,
    *,
    variant,
    chapters="all",
    speech="all",
) -> BundleRenderInspection:
    ...
```

It should expose:

- selected occurrences;
- rendered/reusable occurrences;
- missing occurrences;
- stale occurrences;
- corrupt occurrences;
- aggregate state;
- whether reset/force is safe/required.

## 20.4 VoiceRender acceptance tests

1. memory preflight creates no file;
2. memory preflight result equals persisted preflight semantics;
3. memory RenderPlan execution uses the same engine/session path;
4. memory output occurrence order matches RenderPlan order;
5. partial speech selection works;
6. preflight identity mismatch is rejected;
7. `replan` report cannot be rendered as `ok`;
8. inspector accurately reports partial selected chapter state;
9. inspector is read-only;
10. existing cache/reuse/persisted rendering tests stay unchanged.

**Proposed release:** `voicerender 0.2.9`.

---

# 21. 04 — AudioExport 0.1.6: read-only export inspection

AudioExport already owns:

```text
export_document
export_book
export_bundle
```

and already has strong reuse/identity tests.

No new encoding path is required.

Add:

```python
inspect_bundle_export(
    bundle,
    *,
    variant,
    chapters="all",
    output=None,
    format=None,
    profile=None,
    bitrate=None,
    sample_rate=None,
    channels=None,
    metadata=None,
    cover=None,
    timeline=None,
    ...
) -> BundleExportInspection:
    ...
```

The report should tell Readio whether the exact desired output is:

```text
not_started
complete/reusable
stale
error
```

Identity must include the inputs AudioExport already uses for reuse, such as format/profile/options/metadata/cover/chapter ordering/timeline as appropriate.

The inspector must not run ffmpeg or mutate output.

**Proposed release:** `audioexport 0.1.6`.

---

# 22. 05 — SSMDConvert: no mandatory package change for MVP

The reconstructed SSMDConvert API already provides:

```text
inspect_bundle_source
convert_bundle
load_bundle_workspace
refresh_bundle_workspace
```

The major problem is Readio's policy, not SSMDConvert.

For persistent books, Readio must call:

```python
ssmdconvert.convert_bundle(
    source,
    output=bundle,
    chapters="all",
    ...
)
```

regardless of downstream `RenderRequest.chapters`.

For temporary one-shot mode it may use the selected chapters where supported.

Do not add an incremental "append chapter to bundle" API unless there is a separate demonstrated need. Full persistent source materialization is simpler and makes the bundle a stable canonical source workspace.

**Current package floor:** `ssmdconvert 0.2.4`.

---

# 23. 06 — RenderPlan: no mandatory package change for MVP

RenderPlan 0.2.1 is already the shared contract between current UtterPlan and VoiceRender.

Do not put Readio workflow state into RenderPlan.

Do not make RenderPlan depend on Readio.

Only change RenderPlan if a genuinely shared media contract is missing and belongs conceptually in RenderPlan rather than in one specialist package.

**Current package floor:** `renderplan 0.2.1`.

---

# 24. 07 — Readio 0.6.0 implementation

Only begin this phase once the required specialist APIs have released.

## 24.1 Dependency floor

Recommended Readio 0.6 requirements after the package releases above:

```toml
dependencies = [
  "numpy>=1.23",
  "ssmdconvert>=0.2.4,<0.3",
  "utterplan>=0.6.18,<0.7",
  "voicerender>=0.2.9,<0.3",
  "audiocompose>=0.3.6,<0.4",
  "audioexport>=0.1.6,<0.2",
  "sounddevice>=0.4.6,<1.0",
  "tomli>=2.0; python_version < '3.11'",
]
```

Make AudioExport a core dependency. The target `render` contract promises a final audio file; export is therefore not an optional integration anymore.

Readio should not directly pin RenderPlan unless it imports RenderPlan types as part of its own runtime API. Prefer keeping RenderPlan transitive and specialist-owned.

## 24.2 Add a dependency coherence test

CI must install the released minimum versions together.

This test should fail if:

```text
UtterPlan/VoiceRender require RenderPlan >=0.2
and AudioCompose requires RenderPlan <0.2
```

A resolver conflict of this type should never again reach a Readio release.

---

# 25. Readio source reorganization

Recommended structure:

```text
readio/
  __init__.py

  api/
    __init__.py
    gateway.py
    types.py

  workflow/
    __init__.py
    resolve.py
    inspect.py
    execute.py
    speak.py
    workspace.py
    progress.py
    errors.py

  adapters/
    __init__.py
    ssmdconvert.py
    utterplan.py
    voicerender.py
    audiocompose.py
    audioexport.py

  control/
    engines.py
    models.py
    voices.py
    profiles.py
    runtime.py

  intent.py
  intent_codec.py
  intent_resolver.py

  playback.py
  diagnostics.py
  doctor.py
  cli.py
```

Exact module names are flexible. The dependency direction is not:

```text
CLI
  -> public API
      -> workflow
          -> adapters
              -> specialist packages
```

Never:

```text
workflow -> CLI
adapter -> CLI
specialist package -> Readio
```

---

# 26. Readio cleanup: what to delete or retire

## 26.1 `readio/api/service.py`

Replace the stage-oriented façade.

Old public methods to remove/de-publicize:

```text
create_workspace
ensure_workspace
plan
render_plan
compose
export
run
```

New primary methods:

```text
speak
render
inspect
```

Workspace resolution may remain as a private helper.

## 26.2 `readio/pipeline.py`

Current stage wrappers become redundant.

Either delete the module or turn it into private adapter implementation. Do not keep duplicate public stage entrypoints.

## 26.3 `readio/render_service.py`

Replace with the convergence planner/executor.

Do not preserve the current pattern of constructing all five stage requests and calling every stage unconditionally.

## 26.4 `readio/speech.py`

Delete the direct VoiceRender synthesis path.

If the module remains, it must be a thin wrapper around the new semantic `Readio.speak()` workflow.

Delete the bespoke `_write_speech_wav` path if `speak --output` is removed.

## 26.5 `readio/status.py`

Rewrite around the new inspector contract.

Delete generated "next command" advice such as:

```text
readio bundle compile ...
readio bundle render ...
```

because those commands no longer exist.

Instead report actions the next `readio render` will automatically perform.

## 26.6 `readio/api/types.py`

Remove/deprecate public stage request types:

```text
PlanRequest
RenderPlanRequest
VoiceRenderRequest
ComposeRequest
ExportRequest
RunRequest
PipelineResult
```

Add the workflow types described above.

## 26.7 `readio/api/__init__.py`

Narrow and intentionally curate `__all__`.

Do not re-export every implementation-stage concept.

## 26.8 `readio/intent_resolver.py`

Remove the implicit default persistent bundle path.

No explicit bundle means temporary mode.

## 26.9 `readio/cli.py`

Remove:

```text
bundle convert
bundle compile
bundle render-plan
bundle render
bundle compose
bundle export
bundle run
```

Shrink the parser around workflow and management commands.

`cli_control.py` can largely remain if it is already cleanly isolated.

---

# 27. Status output design

Example:

```text
$ readio status novel.ssmd-bundle --profile narrator.renderprofile.toml --chapters 2

WORKSPACE
  mode              persistent
  kind              book
  source            novel.epub
  selected          chapter 2
  variant           narration--3b5f647f8f57

STAGE         STATE          ACTION
convert       complete       reuse
compile       not_started    compile chapter 2
render-plan   not_started    project chapter 2
render        blocked        after render-plan
compose       blocked        after render
export        blocked        after compose
```

After chapter 2 is complete:

```text
STAGE         STATE          ACTION
convert       complete       reuse
compile       complete       reuse
render-plan   complete       reuse
render        complete       reuse
compose       complete       reuse
export        not_started    export requested output
```

This is much more useful than telling the user which specialist command to run next.

`readio render --dry-run` can use the same `WorkflowPlan` renderer.

---

# 28. Progress and verbosity

Preserve the existing useful global verbosity concept.

All workflow execution should emit normalized progress from one Readio layer while preserving package details under verbose modes.

Recommended behavior:

```text
default:
  stage name + compact progress

-v:
  package, selected scope, reuse/execution decisions

-vv:
  timings, identities, cache/reuse decisions, specialist diagnostics
```

JSON mode:

- structured result on stdout;
- progress/logging on stderr;
- never mix human progress into JSON stdout.

Suggested events:

```python
WorkflowEvent(
    stage="compile",
    kind="stage.started",
    package="utterplan",
    selector="2",
)

WorkflowEvent(
    stage="compile",
    kind="stage.reused",
    package="utterplan",
    selector="1",
)

WorkflowEvent(
    stage="compile",
    kind="stage.completed",
    package="utterplan",
    elapsed_seconds=...,
)
```

Do not translate every package event into a lossy generic percentage. Preserve specialist details in an attached `details` mapping.

---

# 29. Error model

Define workflow-level errors without hiding the specialist cause.

Example hierarchy:

```text
ReadioError
  WorkflowError
    WorkflowResolutionError
    WorkflowStateError
    WorkflowStageError
    WorkflowReplanError
    OutputCollisionError
```

`WorkflowStageError` should carry:

```python
stage
package
source
workspace
variant
selector
cause
```

Human output:

```text
Readio render failed during UtterPlan compile for chapter 2:
<specialist error>
```

Verbose mode may include the full exception chain.

Do not catch and replace specialist errors with generic strings that destroy actionable diagnostics.

---

# 30. Atomicity and ownership

## 30.1 Readio never manually deletes specialist outputs to "repair" them

For stale specialist state:

```text
Readio inspector
    ->
specialist reports stale + safe reset
    ->
Readio calls specialist public force/reset API
```

Do not:

```python
shutil.rmtree(bundle / "voicerender")
```

from generic Readio workflow code.

## 30.2 Final output

AudioExport owns encoding atomicity.

Readio owns the policy about whether an existing user output may be replaced.

## 30.3 Temporary workspace

Use `TemporaryDirectory`.

The final output must not live inside that directory.

---

# 31. Required end-to-end tests

These tests are release-blocking.

## 31.1 Speak uses the semantic chain

Monkeypatch public specialist entrypoints and verify order:

```text
utterplan.compile_document
voicerender load/create profile
utterplan.build_render_plan
voicerender.preflight_render_plan
[optional utterplan.build_render_plan replan loop]
voicerender.render_renderplan_memory
audiocompose.compose_renderplan_memory
```

Assert direct `voicerender.synthesize()` is not called by Readio workflow code.

## 31.2 Speak creates no pipeline artifacts

Run with a controlled empty working directory.

Assert no new:

```text
.ssmd-bundle
utterplan/
renderplan/
voicerender/
audiocompose/
*.wav
*.toml
```

pipeline artifacts are created.

Model/engine caches outside the test workspace may be mocked or redirected.

## 31.3 Speak replanning

Create a fake preflight sequence:

```text
replan -> ok
```

Assert:

- UtterPlan rebuilds RenderPlan;
- second preflight uses new plan;
- renderer receives only the accepted plan.

Also test:

```text
replan -> same plan identity repeatedly
```

and assert bounded failure.

## 31.4 Temporary render

Input:

```text
document source
no --bundle
```

Assert:

- temporary SSMD bundle created;
- complete chain runs;
- final output exists;
- temp tree is removed afterward.

## 31.5 Persistent first chapter

Input:

```text
novel.epub
--bundle novel.ssmd-bundle
--chapters 1
```

Assert SSMDConvert receives:

```text
chapters="all"
```

and downstream packages receive:

```text
chapters="1"
```

Assert chapter 2 source is already present in the persistent bundle.

## 31.6 Persistent second chapter — critical acceptance test

After the previous test:

```text
readio render novel.ssmd-bundle --chapters 2 ...
```

Assert:

- SSMDConvert is not called;
- chapter 1 FlowPlan hash and mtime unchanged;
- UtterPlan compiles only chapter 2 if it was not compiled;
- RenderPlan set extends to include chapter 2;
- chapter 1 RenderPlan content is unchanged;
- VoiceRender renders only missing selected chapter-2 speech;
- AudioCompose composes only required chapter-2 output;
- chapter-1 outputs remain intact.

This test directly proves the requested incremental behavior.

## 31.7 Re-run selected chapter

Run chapter 2 again with identical intent.

Assert all intermediate stages report reuse and do not mutate artifacts.

If the final output path is different, only export should execute.

## 31.8 Source chapter changed

Change chapter 2 source in a controlled bundle fixture.

Assert:

- chapter 1 remains complete;
- chapter 2 compile/projection/downstream state is stale;
- workflow rebuilds only chapter 2;
- no broad bundle deletion.

## 31.9 Profile changed

Render with profile A, then profile B.

Assert:

- profile B gets a distinct validated variant identity;
- profile A variant remains;
- Readio never forces profile B into profile A's variant.

## 31.10 Existing output

Assert:

```text
output exists + no --replace-output -> fail before destructive work
output exists + --replace-output    -> allowed
```

## 31.11 Inspector purity

For each specialist inspector:

1. hash/tree snapshot before;
2. call inspector;
3. snapshot after;
4. exact equality.

## 31.12 CLI surface

Assert help contains:

```text
speak
render
status
intent
engines
models
voices
profile
runtime
doctor
```

and does **not** contain a stage-running `bundle` command.

Also assert these are absent:

```text
readio compile
readio compose
readio export
readio render-plan
```

## 31.13 API boundary

Static or runtime test:

- Readio workflow imports only public specialist APIs;
- no imports from specialist private modules;
- no direct parsing of specialist manifests for stage state.

---

# 32. Integration test matrix

At minimum:

| Scenario                 | Workspace  | Selection | Expected                              |
| ------------------------ | ---------- | --------- | ------------------------------------- |
| plain text speak         | memory     | n/a       | audio buffer, no artifacts            |
| document render          | temp       | all       | output, no persistent bundle          |
| book render first        | persistent | chapter 1 | full source bundle, chapter 1 derived |
| book render later        | persistent | chapter 2 | chapter 1 reused untouched            |
| book render combined     | persistent | 1-2       | derived state reused                  |
| changed source item      | persistent | chapter 2 | only dependent item rebuilt           |
| changed profile          | persistent | chapter 2 | new profile variant                   |
| stale renderer artifact  | persistent | chapter 2 | specialist-safe rebuild               |
| corrupt unknown artifact | persistent | chapter 2 | fail closed                           |
| output collision         | either     | any       | fail unless explicit replacement      |

---

# 33. Documentation rewrite

The 0.6 README should introduce exactly two media workflows first:

## Speak

```bash
readio speak --voice kokoro:v1.0/af_heart --lang en-US \
  "Hello, how are you?"
```

Explain:

```text
Text -> semantic planning -> voice rendering -> composition -> playback
No Readio pipeline artifacts are persisted.
```

## Render

```bash
readio render article.docx \
  --profile narrator.renderprofile.toml \
  -o article.mp3
```

Explain default temporary workspace.

Then persistent book example:

```bash
readio render novel.epub \
  --bundle novel.ssmd-bundle \
  --profile narrator.renderprofile.toml \
  --chapters 1 \
  -o chapter-01.m4b

readio render novel.ssmd-bundle \
  --profile narrator.renderprofile.toml \
  --chapters 2 \
  -o chapter-02.m4b
```

Explicitly explain:

> A persistent book bundle is populated with the complete source on first creation. `--chapters` scopes downstream work. Rendering chapter 2 later does not require reconverting the book or recompiling chapter 1.

Then document intent creation and control commands.

Move manual package-stage examples into a section titled:

```text
Specialist debugging
```

and show the specialist CLIs there, not Readio stage commands.

---

# 34. Changelog for Readio 0.6.0

Suggested entry:

```markdown
## 0.6.0

### Breaking

- Reworked Readio as a workflow gateway over SSMDConvert, UtterPlan,
  VoiceRender, AudioCompose, and AudioExport.
- Removed Readio's stage-oriented `bundle convert/compile/render-plan/ render/compose/export/run` CLI.
- `render` now uses temporary intermediate storage unless a bundle is
  explicitly supplied or SOURCE is already an SSMD bundle.
- `speak` now uses the UtterPlan -> RenderPlan -> VoiceRender ->
  AudioCompose semantic chain instead of direct engine synthesis.
- Removed/de-publicized stage request types from the primary Readio API.
- Intent schema updated for explicit temporary/persistent workspace semantics.

### Added

- Unified `Readio.speak`, `Readio.render`, and `Readio.inspect`.
- Persistent incremental book rendering across chapter selections.
- Workflow dry-run/convergence plans.
- Read-only normalized stage inspection.
- AudioExport as a core render dependency.

### Changed

- Persistent book bundle creation materializes the complete source inventory;
  chapter selection scopes only downstream derived work.
- Rebuild controls are workflow-oriented rather than manual stage commands.
```

---

# 35. Suggested implementation sequence

Do not try to rewrite all repositories at once.

Use this order:

```text
01  AudioCompose 0.3.6
    - RenderPlan 0.2 compatibility
    - in-memory RenderPlan composition
    - bundle composition inspector

02  UtterPlan 0.6.18
    - compile inspector
    - RenderPlan projection inspector

03  VoiceRender 0.2.9
    - non-persisting preflight
    - in-memory RenderPlan rendering
    - selection-aware render inspector

04  AudioExport 0.1.6
    - export inspector

05  Verify/install released package set together
    - dependency coherence test

06  Readio 0.6.0 API/workflow core
    - new workflow types
    - workspace policy
    - convergence planner
    - convergence executor

07  Readio speak
    - semantic in-memory chain
    - bounded replan loop
    - playback adapter

08  Readio render
    - temp mode
    - persistent full-source bundle mode
    - incremental selected convergence

09  Readio CLI cleanup
    - delete stage-oriented bundle commands
    - retain control commands
    - render --dry-run/status

10  Intent v2 + docs + changelog

11  End-to-end and dependency-floor release tests
```

Each numbered step should be mergeable and testable before the next begins.

---

# 36. Definition of done

Readio 0.6 is not done until all of the following are true:

- [ ] current Readio version history is preserved; release is 0.6.0 or newer;
- [ ] AudioCompose supports RenderPlan 0.2.1 and the dependency set resolves;
- [ ] `Readio.speak()` uses UtterPlan + RenderPlan + VoiceRender + AudioCompose;
- [ ] `speak` creates no Readio pipeline artifacts;
- [ ] `Readio.render()` is the only normal file-to-file media workflow;
- [ ] render without `--bundle` uses a temporary workspace;
- [ ] render with an explicit new persistent book bundle converts the full source inventory;
- [ ] later chapter rendering does not reconvert the source;
- [ ] later chapter rendering does not recompile already-valid earlier chapters;
- [ ] RenderPlan sets extend incrementally where supported;
- [ ] specialist packages, not Readio, decide artifact validity/staleness;
- [ ] automatic convergence never deletes unknown/corrupt user data;
- [ ] final output replacement requires explicit permission;
- [ ] Readio CLI contains no individual conversion/compile/render-plan/compose/export pipeline commands;
- [ ] engine/model/voice/profile/runtime management remains available;
- [ ] intent TOML can be created by CLI and reviewed by a human;
- [ ] `--dry-run` and status use the same convergence planner as real render;
- [ ] package-root public API boundaries are tested;
- [ ] documentation teaches `speak` and `render` first;
- [ ] there is no `.readio` project-state directory.

---

# 37. Final architectural rule

The simplest way to judge every proposed 0.6 change is this:

> **Readio decides what the user wants and which specialist operation must happen next. The specialist package decides how that operation works and whether its own artifacts are valid.**

That gives the intended clean split:

```text
Readio
  = gateway + policy + convergence + unified UX

SSMDConvert
  = source conversion

UtterPlan
  = semantic speech planning + RenderPlan projection

RenderPlan
  = shared contract

VoiceRender
  = speech rendering

AudioCompose
  = temporal/audio composition

AudioExport
  = final media encoding and verification
```

And the user-facing result becomes exactly the two workflows from the notebook:

```text
TEXT
  -> Readio.speak()
  -> audio in memory
  -> sound

FILE
  -> Readio.render()
  -> temporary or persistent SSMD workspace
  -> incremental converged pipeline
  -> final audio file
```
