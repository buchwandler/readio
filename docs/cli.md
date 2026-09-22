# CLI reference: project pipeline

## Project lifecycle

```text
readio project init SOURCE -o PROJECT
readio plan PROJECT
readio synth PROJECT [--engine ENGINE] [--voice VOICE] [--select SELECTOR]
readio preview PROJECT --select SELECTOR [--voice VOICE] [-o PREVIEW.wav]
readio compose PROJECT [--target-lufs FLOAT]
readio export PROJECT --format {wav,mp3,m4a,ogg}
readio status PROJECT [--json]
readio render PROJECT --format FORMAT
```

## Persistent project status

`readio status` discovers the project by walking upward from the current directory. Human output shows the project root, source format, each stage state/reason, dependency blocking, and the primary next command. Structured output preserves stable reason codes and includes `issues` plus `next_actions`.

Plan artifact reasons include `plan.index.missing`, `plan.index.invalid`, `plan.artifact.missing`, `plan.artifact.hash_mismatch`, `plan.artifact.plan_id_mismatch`, and `plan.stale.document_format_mismatch`. A wrong-format legacy plan is therefore rebuilt with `readio plan` instead of being silently reused.

## Synthesis progress and JSON

Project `synth` and `preview` accept the shared `--progress` / `--no-progress` option. Interactive progress is written to stderr and includes the resolved profile, cache counts, model-loading phase, and unit/segment preview. `-v` and `-vv` select the existing INFO and DEBUG logging levels; use them for bounded stage, runtime, timing, and cache diagnostics.

`readio synth --json` emits one final JSON object on stdout. It includes the project, scope, plan ID, profile identity and engine/model/voice/language, selector/count, and cache reuse/render counts. Progress and logs remain on stderr, and automatic progress is disabled for JSON unless explicitly forced.

`plan` is semantic and engine-free for a project or when its output ends in
`.utterplan.json`. Ordinary text `plan` and `render --dry-run` retain the
existing resolved execution-plan inspection behavior. Non-project `speak` and
`render` remain supported.
