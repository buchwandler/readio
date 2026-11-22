---
name: readio
description: Use Readio v0.7 platform contracts for unified speech workflows, read-only capability discovery, cooperative cancellation, and optional execution reports.
compatibility: Requires Readio and specialist packages for the selected workflow. Playback requires an audio device; synthesis requires an installed VoiceRender engine.
---

# Readio

Use Readio's user-owned `readio.render.v3` intent for complete file-to-audio workflows. Keep the intent TOML separate from the VoiceRender RenderProfile. Readio is an orchestration gateway; specialists own their schemas, validation, artifacts, engines, composition, and export behavior.

## Workflow decisions

1. **Exact speech:** Do not paraphrase requested text. Use `readio speak` or `readio.api.speak` for literal text.
2. **Complete render:** Create/review an intent with `readio intent book|document create`, then run `readio render --config PATH`. Use `readio intent show` and `validate` before rendering.
3. **One-off render:** Use `readio render SOURCE --profile PROFILE` when a saved intent is unnecessary; the VoiceRender RenderProfile remains a separate input.
4. **Temporary by default:** Without an explicit `--bundle`, Readio uses a unique temporary workspace and cleans it after execution. It never invents a persistent bundle path or hidden `.readio/` state.
5. **Persistent state:** Supply `--bundle PATH`, set `[workspace] mode = "bundle"` with `workspace.bundle` in an intent, or pass an existing `.ssmd-bundle` as the source. Persistent book creation converts the complete source inventory; chapter selection scopes downstream work.
6. **Inspect before execution:** `readio render --dry-run` and `readio status` share read-only workflow inspection. Use `readio status --config PATH` for a saved intent or `readio status BUNDLE --profile PROFILE` for an existing bundle.
7. **Safe reruns:** Use `--rebuild-from STAGE` for explicit rebuilds and `--replace-output` separately to authorize final-output replacement. Specialist inspection decides what is safe; Readio fails closed on unknown unsafe state.
8. **Stage identity:** Preserve UtterPlan's exact projected variant through VoiceRender, AudioCompose, and AudioExport; never invent or normalize it.
9. **Profiles and agents:** Profile/engine commands delegate to VoiceRender. Use explicit user-owned intent, profile, bundle, and output paths. Progress is on stderr; use `--json` for machine-readable results.

## Public platform contracts

`readio.api` is additive across compatible minor updates. Existing constructors and call patterns remain supported; new dataclass fields have defaults and new execution options are keyword-only. Workflow plans, render results, normalized errors, and aggregate capabilities use versioned Readio JSON envelopes. Use `readio.api.serialize_public()` or the specific `*_to_dict()` helper instead of building protocol JSON from diagnostics or specialist messages.

The canonical workflow plan is used by inspection, status, and dry-run. Its reason codes, blockers, and logical artifact references are Readio-owned; `details` is diagnostic-only. Structured errors use stable Readio codes and recovery guidance, while specialist diagnostic codes remain nested details.

## Capability discovery

Use the read-only gateway methods for GUI or agent startup:

```python
from readio.api import Readio

readio = Readio()
readio.capabilities()
readio.source_capabilities("novel.epub")
readio.synthesis_capabilities()
readio.export_capabilities()
```

Capability calls do not install or download models, open a synthesis model, create a workspace or bundle, mutate runtime-provider policy, create profiles, or write files. Source capabilities inspect one concrete source. Readio does not claim a global source-format list that SSMDConvert does not expose through a stable public API.

## Cancellation and execution reports

Pass a `CancellationToken` to `Readio.render()` or `Readio.render_intent()` to request cooperative cancellation at Readio-controlled stage boundaries and supported progress callbacks. Cancellation raises a structured `WorkflowCancelledError`. Temporary workspaces are cleaned up; completed persistent specialist artifacts are not rolled back. AudioExport currently has no public mid-call cancellation hook, so cancellation requested during export is observed only after its public call returns.

Use `include_report=True` only when in-memory stage timings and specialist version metadata are useful. `RenderResult.report` is optional and is never written to a file, database, history store, or workspace.

## Unified workflow commands

```bash
python -m pip install readio
python -m pip install "readio[kokoro]"

readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narrator.renderprofile.toml
readio intent document create chapter.docx \
  --profile narrator.renderprofile.toml --language en-US
readio intent show chapter.readio.toml --resolved
readio intent validate chapter.readio.toml
readio render --config chapter.readio.toml --dry-run
readio render --config chapter.readio.toml
readio status --config chapter.readio.toml

# Persistent workspace; first book conversion includes every source chapter.
readio render novel.epub --bundle novel.ssmd-bundle \
  --profile narrator.renderprofile.toml --language en-US --chapters 5-17
```

Book intents default to M4B; document intents default to MP3. Intent TOML is user-owned and separate from the VoiceRender RenderProfile. AudioExport is included in the core installation; Readio checks selected format and tool readiness before conversion.

The complete command catalog is in [CLI reference](references/cli.md). Input handling is covered in [Input formats](references/input-formats.md); specialist ownership and manual workflows are covered in [SSMD](references/ssmd.md).

## Specialist workflow

For direct stage control, pass UtterPlan's exact variant unchanged:

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

SSMDConvert owns conversion and bundles. UtterPlan owns semantic plans and variants. RenderPlan owns its public contract. VoiceRender owns profiles, voice/engine management, and synthesis. AudioCompose owns composition and audio-job artifacts. AudioExport owns encoding.

## Explicit boundaries

Use TTSForge or host applications for audiobook project/library management; Readio does not provide audiobook project management. Readio also does not add TTSForge imports, specialist-private API access, backend SDK imports, hidden persistence, job queues, daemons, worker pools, or background hosts. Use specialist tools directly when the task needs specialist-owned schemas or package-specific stage control.
