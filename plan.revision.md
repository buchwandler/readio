---
goal: Implement the Readio 0.6 workflow gateway and the missing specialist APIs it
  depends on, with convergent render, semantic in-memory speak, safe workspace policy,
  and curated public interfaces.
files:
- '@../audiocompose/audiocompose'
- '@../utterplan/utterplan'
- '@../voicerender/voicerender'
- '@../audioexport/audioexport'
- '@../ssmdconvert/ssmdconvert'
- '@pyproject.toml'
- '@readio'
- '@tests'
- '@README.md'
- '@docs'
test_commands:
- pytest -q ../audiocompose/tests/test_compose_renderplan_api.py ../audiocompose/tests/test_bundle_batch_compose.py
- pytest -q ../utterplan/tests/test_bundle_book_services.py ../utterplan/tests/test_renderplan_replan.py
- pytest -q ../voicerender/tests/test_preflight.py ../voicerender/tests/test_renderplan_render.py
  ../voicerender/tests/test_bundle_inspect.py
- pytest -q ../audioexport/tests/test_bundle.py ../audioexport/tests/test_preflight.py
- pytest -q
expected_outputs:
- Specialist inspectors are read-only and report selected complete, missing, stale,
  corrupt, and unsafe states without inferring validity from filenames.
- Readio speak follows UtterPlan -> RenderPlan -> VoiceRender -> AudioCompose entirely
  in memory and persists no pipeline artifacts.
- Readio render uses temporary workspaces by default and incrementally reuses explicitly
  persistent bundles.
- The Readio CLI/API expose workflow and control operations, not individual media-stage
  commands.
- Targeted specialist suites and the full Readio test suite pass.
generation_reason: after_questions
acceptance_criteria:
- id: ac-0001
  text: AudioCompose, UtterPlan, VoiceRender, and AudioExport expose the public memory/inspection
    APIs required by the brief; inspection is read-only, selection-aware, identity-aware,
    and does not infer completion from file existence.
  mandatory: true
- id: ac-0002
  text: Readio.speak uses UtterPlan compilation, an in-memory or loaded RenderProfile,
    RenderPlan construction, non-persisting preflight and bounded replanning, VoiceRender
    in-memory rendering, and AudioCompose in-memory composition; no Readio pipeline
    artifacts are written.
  mandatory: true
- id: ac-0003
  text: Readio.render and Readio.inspect share workspace resolution and a read-only
    convergence plan; default rendering is temporary, explicit bundles are persistent,
    persistent book creation converts all source chapters, and subsequent selections reuse
    valid work without reconverting or rewriting earlier chapters.
  mandatory: true
- id: ac-0004
  text: Automatic convergence uses specialist-owned inspections and safe replacement
    operations, fails closed for unknown/corrupt state, validates source provenance,
    and never deletes specialist data or replaces an existing output without explicit
    permission.
  mandatory: true
- id: ac-0005
  text: The stable Readio API is centered on speak/render/inspect plus unified engine/model/voice/profile/runtime
    controls; stage-oriented request types and stage-running CLI commands are removed
    from the primary surface.
  mandatory: true
- id: ac-0006
  text: Intent TOML v2 and CLI override precedence support explicit temporary/persistent
    workspaces, workflow rebuild controls, dry-run/status, normalized progress/errors,
    and specialist CLI guidance without creating a .readio project-state directory.
  mandatory: true
- id: ac-0007
  text: Dependency floors and coherence checks select a compatible released specialist
    stack, AudioExport is a core dependency, and Readio release metadata preserves
    history while targeting 0.6.0 or newer.
  mandatory: true
- id: ac-0008
  text: The rewritten documentation teaches speak and render first, explains persistent
    incremental book behavior and specialist debugging boundaries, and automated regression
    coverage exercises the brief's critical end-to-end and API-boundary scenarios.
  mandatory: true
todos:
- id: plan-todo-0001
  text: Add AudioCompose in-memory RenderPlan composition and read-only composition
    inspection using its existing materialization/composer pipeline; add semantic-equivalence,
    identity, and purity tests without touching AudioCompose Taskledger state.
  mandatory: true
  validation_hint: Run focused AudioCompose memory-composition, RenderPlan materialization,
    and persisted reuse tests.
- id: plan-todo-0002
  text: Add UtterPlan read-only bundle compile and RenderPlan projection inspectors
    that report per-selection freshness, profile identity, and the safe missing/stale
    execution subset.
  mandatory: true
  validation_hint: Run UtterPlan inspector, incremental projection, profile-identity,
    and read-only snapshot tests.
- id: plan-todo-0003
  text: Add VoiceRender non-persisting preflight, in-memory RenderPlan execution sharing
    the persisted execution core, and selection-aware persisted render inspection.
  mandatory: true
  validation_hint: Run VoiceRender preflight, memory execution, session/cache reuse,
    selection, identity, and inspector-purity tests.
- id: plan-todo-0004
  text: Add AudioExport read-only output inspection keyed to its existing complete
    export identity and reuse rules.
  mandatory: true
  validation_hint: Run AudioExport bundle inspection tests and verify inspector calls
    do not invoke ffmpeg or mutate output state.
- id: plan-todo-0005
  text: Implement Readio workflow request/result types, curated public API, workspace
    policy, specialist adapters, normalized workflow plans/states, convergence safety,
    errors, and progress.
  mandatory: true
  validation_hint: Run focused Readio API, adapter, workspace, status, progress, and
    dependency-boundary tests.
- id: plan-todo-0006
  text: Replace direct synthesis in Readio speak with the in-memory semantic pipeline,
    in-memory profile creation, bounded identity-checked replanning, AudioBuffer results,
    and playback without pipeline files.
  mandatory: true
  validation_hint: Run speak call-order, no-artifact, profile/direct-voice, replan-loop,
    error, and playback tests.
- id: plan-todo-0007
  text: Implement temporary and explicit persistent render workflows, source provenance
    checks, full-source persistent book conversion, incremental selected-stage convergence,
    safe rebuild controls, output collision policy, inspect, and dry-run.
  mandatory: true
  validation_hint: Run temporary cleanup, persistent chapter-1/chapter-2 reuse, changed-source/profile,
    output-collision, stale/corrupt fail-closed, and dry-run purity tests.
- id: plan-todo-0008
  text: Migrate intent/config to explicit workspace v2 semantics; simplify the CLI
    to speak/render/status/intent and control/doctor commands; remove stage-runner
    commands and stage-oriented public exports.
  mandatory: true
  validation_hint: Run CLI help/API-boundary, intent v2 round-trip/precedence, control
    command, and legacy stage-command absence tests.
- id: plan-todo-0009
  text: Update Readio dependency floors/coherence CI, docs and 0.6.0 release notes/metadata,
    then run targeted specialist tests, dependency resolution, and the full Readio
    suite.
  mandatory: true
  validation_hint: Run all changed package suites, dependency-coherence smoke, packaging
    checks, and pytest -q; inspect the 0.6.0 changelog and release status.
---

<!-- Human-facing implementation handoff. -->

# Readio 0.6 workflow gateway and specialist API rework

## Summary

Rework Readio into the single user-facing workflow gateway described by `@readio_0_6_gateway_rework_agent_brief.md`, while keeping conversion, planning, synthesis, composition, and export implementation in their specialist packages. Implement the missing specialist public inspection and in-memory APIs first, in the brief's dependency order, then use those APIs from a convergent Readio workflow. This is a breaking 0.x redesign targeting Readio 0.6.0 or newer; preserve the existing release history and do not invent a release date or publish/tag packages as part of this implementation.

## Implementation Changes

- AudioCompose: add RenderPlan 0.2-compatible memory composition through existing materialization and `Composer.compose` paths, plus read-only selected bundle composition inspection. Do not modify any AudioCompose `.ledger/taskledger` state; the user explicitly directed that task-0029's ledger remain untouched.
- UtterPlan: add read-only per-item compile and RenderPlan projection inspectors, including freshness, selected/missing subsets, and profile/snapshot identity.
- VoiceRender: make preflight persistence optional; add in-memory RenderPlan rendering through shared execution logic and selection-aware bundle inspection.
- AudioExport: add read-only inspection of the exact requested output identity without encoding or mutation.
- Readio: replace stage-oriented service/types with the workflow API, workspace policy, adapters, convergence plan/executor, safe fail-closed state handling, in-memory semantic `speak`, temporary/persistent incremental `render`, intent v2, CLI cleanup, normalized progress/errors, tests, dependency floors, and documentation.

## Tests

- Specialist targeted suites for memory composition, compile/projection/render/export inspection, identity, incremental reuse, and inspector purity.
- Readio tests for semantic speak order and no artifacts; bounded replanning; temporary cleanup; persistent all-source conversion and incremental chapter reuse; changed source/profile; output collision; safe rebuild; dry-run purity; CLI/API boundaries; and intent precedence.
- Dependency-coherence installation/resolution check and the complete Readio test suite, plus package-specific regression suites and packaging checks.

## Assumptions

- The sibling package checkouts named by the brief are the intended source repositories and may be edited for the required APIs, as explicitly authorized in chat.
- The AudioCompose taskledger state is out of bounds: do not activate, resume, validate, or otherwise mutate task-0029's ledger. Record the API work under this Readio task and use code/test evidence.
- Specialist changes must precede Readio integration. Do not emulate specialist behavior or inspect their private artifact formats from Readio.
- Release versions in the brief are targets; actual publishing, remote pushes, and Git tag creation are not authorized by this implementation task.

## Out of Scope

- Publishing packages, pushing commits, creating Git tags, or inventing release dates.
- Rewriting specialist algorithms beyond the requested in-memory/inspection APIs and dependency compatibility.
- Creating or mutating sibling-project Taskledger records, especially AudioCompose task-0029.
- Introducing hidden persistent `.readio` state or retaining Readio's stage-oriented CLI as a compatibility namespace.

## Notes from answered questions

- q-0001: Leave the audiocompose ledger untouched; continue with the Readio checkout and treat existing AudioCompose code as a prerequisite.
- q-0002: Implement the missing public APIs in sibling package source checkouts as needed; leave the AudioCompose Taskledger state untouched.
