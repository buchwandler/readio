# CLI and JSON

## Common commands

```bash
readio speak --file document.md
readio render --file document.md --format mp3 --output episode.mp3
readio render --input-format markdown
readio ingest new --name summary.txt
```

Input can be literal text, a UTF-8 file, or stdin. `--live` consumes plain piped text incrementally and cannot be combined with Markdown/SSMD parsing, `--file`, or selection.

## Machine mode

Agents should use `--json`. It is accepted before or after the command, and literal text after `--` is preserved. Successful commands emit one object with `ok: true`; JSON errors emit one object containing `ok: false`, a stable `code`, and human-readable `error`. Progress is disabled automatically in JSON mode. Explicit `--progress` writes only to stderr.

`render --json` reports `path`, `format`, `sample_rate`, `sample_count`, `channels`, `duration_ms`, and `markers`. Human render output remains the final path for shell compatibility.

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
