# CLI reference: project pipeline

## Project lifecycle

```text
readio project init SOURCE -o PROJECT
readio plan                         # build current project
readio plan build [PROJECT]
readio plan roles [PROJECT]
readio plan bind ROLE VOICE [--project PROJECT]
readio plan unbind ROLE [--project PROJECT]
readio synth PROJECT [--engine ENGINE] [--voice VOICE] [--select SELECTOR]
readio preview PROJECT --select SELECTOR [--voice VOICE] [-o PREVIEW.wav]
readio compose PROJECT [--target-lufs FLOAT] [--progress | --no-progress] [--json]
readio export PROJECT --format {wav,mp3,m4a,ogg}
readio status PROJECT [--json]
readio render PROJECT --format FORMAT
```

`readio plan` manages persistent project semantics and roles. Running it without a subcommand builds the project in the current directory; it does not inspect one-shot text or choose an engine. Use `readio render --dry-run` for one-shot execution planning:

```bash
readio render --file episode.ssmd --format mp3 --dry-run --json
```

## Project voice provider and routing

`project.json` can select an active provider at `settings.ssmd.voice_provider`. Existing projects without that field infer the provider from a single non-empty `voice_bindings` namespace. Projects with neither an active provider nor project binding namespaces keep the global configuration fallback. Multiple provider namespaces without an active provider are ambiguous and must be resolved explicitly. `readio plan bind` can activate a provider from a stable selector, and `readio plan roles` reports bindings from the effective provider.

With no explicit engine, project synthesis selects the engine associated with that provider. It does not inherit global `reader.engine` or `reader.voice` over an active project provider. `readio synth --engine ENGINE` is a run-local override; it never writes project settings. Use `--voice` for a concrete run-local voice override.

```bash
readio plan bind narrator en-pi-13
readio plan roles
readio synth
readio synth --engine pykokoro  # one-run override
```

PyKokoro is runtime-bound and can switch voices in one loaded pipeline. Piper is target-bound. Readio resolves every role to a Piper voice target and validates all targets before model loading, then reuses one session per distinct target. Progress reports each target's load separately. Bindings affect synthesis, not the semantic plan ID.

## Voice catalog filters

For `readio voices list`, registered engine/system names passed as `--model` are shortcuts only when `--engine` is omitted: `piper` and `pipersynth` select Piper, while `pykokoro` and `kokoro` select PyKokoro. The JSON `filters` object reports the effective engine and clears the model field for such shortcuts. Concrete model IDs and Piper target IDs remain model filters.

```bash
readio voices list --model piper --lang en-us
readio voices list --engine piper --model en_US-amy-medium
```

## EPUB audiobook ingestion

```text
readio audiobook chapters EPUB [--json]
readio audiobook init EPUB [--chapters SPEC] [-o PROJECT] [--json]
```

`chapters` reports flat 1-based chapter numbers, titles, and EPUB metadata. `init` defaults to all chapters and otherwise accepts comma-separated numbers and inclusive ranges such as `2-4,7`. The selected chapter numbers are persisted in the new project, so `plan`, `synth`, `compose`, `export`, `preview`, and `render` use the project selection without an EPUB-specific downstream option.

Initialization copies the EPUB as provenance and writes each selected chapter as editable Markdown under `document/chapters/`. The Markdown is the semantic input. Editing it does not re-extract the EPUB. If the copied EPUB hash changes, status reports `source.stale.hash_changed`; reinitialize from the updated EPUB instead of silently remapping old chapter numbers. EPUB is not accepted as a direct `InputDocument` format and does not use a separate audiobook renderer.

This first EPUB workflow does not implement M4B encoding or embedded container chapter metadata.

## Persistent project status

`readio status` discovers the project by walking upward from the current directory. Human output shows the project root, source format, each stage state/reason, dependency blocking, and the primary next command. Structured output preserves stable reason codes and includes `issues` plus `next_actions`.

Plan artifact reasons include `plan.index.missing`, `plan.index.invalid`, `plan.artifact.missing`, `plan.artifact.hash_mismatch`, `plan.artifact.plan_id_mismatch`, and `plan.stale.document_format_mismatch`. A wrong-format legacy plan is therefore rebuilt with `readio plan` instead of being silently reused.

## Synthesis progress and JSON

Project `synth` and `preview` accept the shared `--progress` / `--no-progress` option. Interactive progress is written to stderr and includes the resolved profile, cache counts, model-loading phase, and unit/segment preview. `-v` and `-vv` select the existing INFO and DEBUG logging levels; use them for bounded stage, runtime, timing, and cache diagnostics.

`readio synth --json` emits one final JSON object on stdout. It includes the project, scope, plan ID, profile identity and engine/model/voice/language, selector/count, and cache reuse/render counts. Progress and logs remain on stderr, and automatic progress is disabled for JSON unless explicitly forced.

## Composition progress

`readio compose PROJECT` shares the progress policy with synthesis, preview, render, and project rendering. Progress is enabled automatically on an interactive terminal, can be forced with `--progress`, and can be disabled with `--no-progress`. All progress is written to stderr.

Composition events identify the current speech segment and operation, show completed and total segments, and show an approximate ETA only after enough segment processing has completed. The ETA covers segment processing and does not predict assembly, complete-output loudness or true-peak processing, or artifact writing. Those stages are rendered separately so all segments reaching 100 percent does not imply that the master is complete.

With `--json`, stdout remains one JSON document. Explicit progress remains on stderr, and progress callbacks are runtime observations only. They do not enter composition IDs, AudioJob serialization, timelines, or composition state identities.

`readio plan` is a project command family: `build` creates semantic Utterplan artifacts, `roles` inspects SSMD roles, and `bind` / `unbind` manage project-local acoustic settings. It never selects an engine or loads TTS. Use `readio render --dry-run` to inspect the complete execution plan for one-shot input.
