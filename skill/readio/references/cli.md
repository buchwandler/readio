# CLI and JSON

## Common commands

```bash
readio speak --file document.md
readio render --file document.md --format mp3 --output episode.mp3
readio render --input-format markdown
readio ingest new --name summary.txt
```

Input can be literal text, a UTF-8 file, or stdin. For convenience, `speak`, `render`, and `spotify publish` treat one existing positional token as a file path, including paths with spaces when quoted. For scripts, prefer the explicit `--file PATH` form.

A missing path-like positional token is rejected instead of being synthesized as literal speech. Use `--input-format text` to speak an existing filename literally; explicit `markdown` and `ssmd` formats still enable file loading. Positional input plus `--file` is always an error. `--live` consumes plain piped text incrementally and cannot be combined with Markdown/SSMD parsing, `--file`, or selection.

## Machine mode

Agents should use `--json`. It is accepted before or after the command, and literal text after `--` is preserved. Successful commands emit one object with `ok: true`; JSON errors emit one object containing `ok: false`, a stable `code`, and human-readable `error`. Progress is disabled automatically in JSON mode. Explicit `--progress` writes only to stderr.

`render --json` reports `path`, `format`, `sample_rate`, `sample_count`, `channels`, `duration_ms`, and `markers`. Human render output remains the final path for shell compatibility.

## Synthesis planning

`readio plan` and `readio render --dry-run` resolve the exact plan a render would execute, without loading TTS. Normal `readio render` resolves the same plan first and executes it — the plan is the execution contract:

```bash
readio plan --file input.ssmd --format mp3 --json
readio render --file input.ssmd --dry-run --json
readio plan --file input.ssmd -o episode.mp3 --force --json
```

`--json` emits one `readio.plan.v1` object:

- `ok`: false means rendering would fail now; `diagnostics` carries stable codes such as `model_not_found`, `model_language_incompatible`, `model_runtime_unavailable`, `voice_unavailable`, `quality_unavailable`, `lexicon_unavailable`, `experimental_frontend_disallowed`, `synthesis_incomplete`, `ssmd_unresolved_voice`, `ssmd_voice_unavailable`, `backend_resolution_failed`, `output_format_conflict`, `encoder_unavailable`, and `output_exists` (warning).
- `synthesis.model`: the concrete model with `status`, `runtime_available`, `languages`, `experimental`, voice/quality rosters.
- `ssmd.bindings`: every executable reference -> voice mapping with `origin` (`document`, `cli`, `config.voice_role`, `direct`); `ssmd.unresolved` lists unrenderable references.
- `output`: resolved `format`, `encoder_backend`, `path` (generated paths allocated once, `path_origin: "generated"`), and `force`.
- `decisions`: the winning source (`origin` + `locator`) for every effective value, including each `ssmd.bindings.<ref>` entry.
- `environment`: Readio/PyKokoro/SSMD versions and `ffmpeg_available`.

Exit code is 0 for `ok: true` and 1 for a rejected plan. Planning is deterministic: `--resolve-voices` is rejected by `plan` and `--dry-run` — use `--voice-bind ROLE=VOICE_ID` or persisted `readio voices bind` roles.

## Model discovery

Use PyKokoro 0.9.x metadata to inspect all available runtime models; discovery is metadata-only and does not download weights or voices:

```bash
readio models list --language de --offline --json
readio models list --status ready --offline
readio models show de-thorsten --offline --json
readio voices list --model de-thorsten --json
readio models list --preference huggingface --json
readio voices list --model de-thorsten --preference github --json
```

`--refresh` updates registry metadata only and cannot be combined with `--offline`. JSON includes registry provenance, cache fallback, model status, voice/default voice, qualities, G2P backend, frontend, experimental state, runtime availability, redistribution policy, and `lexicons_known`. `lexicons: null` means the capability is unknown; `lexicons: []` means the model has no named lexicons.
Use `--preference auto|github|huggingface|upstream` for deterministic discovery views. Synthesis/default `--model-source github|huggingface` selects the same distribution for metadata validation and `PipelineConfig`. Voices are model-scoped; SSMD checks the active model roster, not the legacy configured list.

## Language defaults

`defaults` persists validated user choices independently of the runtime catalog:

```bash
readio defaults set de --model de-thorsten --lexicon crane --offline --json
readio defaults show de-at --json
readio defaults list --json
readio defaults reset de
```

When a model is selected, Readio fills source, default voice, and preferred quality from discovery. Exact locale profiles override base-language profiles. `speak`, `render`, and `spotify publish` share `--model`, `--model-source`, `--quality`, `--voice`, repeatable `--lexicon`, `--no-lexicons`, and `--allow-experimental`; explicit options override persisted defaults. Prefer discovery JSON over embedded model or voice lists in agent workflows.

## Audio

Supported formats are WAV, MP3, M4A, and OGG. Infer the format from an output suffix where possible; explicit format and suffix must agree. M4A requires FFmpeg. Use `--force` to replace an existing output.

## Diagnostics

```bash
readio doctor
readio doctor --json
readio config init
readio config validate
```

The local doctor is offline/local. `readio --json doctor` is equivalent to `readio doctor --json`.
`doctor --json` reports PyKokoro's imported module path, distribution/module versions, and public discovery symbol status. Use it to diagnose stale editable checkouts or mismatched environments before treating a registry error as a network/cache problem.
