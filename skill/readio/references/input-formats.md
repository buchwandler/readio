# Input formats

- Plain text is the default for literal narration, summaries, recaps, and status reports.
- Markdown files are recognized by suffix in automatic mode. Readio projects headings, lists, links, images, code blocks, block quotes, tables, task lists, HTML text, and front matter into speech-friendly text.
- Use `--input-format markdown` for generated or piped Markdown and `--input-format text` to force literal reading.
- Complete Markdown and SSMD documents cannot use `--live`; live mode is plain text only.
- Do not manually strip Markdown before passing a local Markdown file. Use SSMD only when the request needs roles, multiple speakers, explicit prosody, or markers.

```bash
readio speak --file guide.md
producer | readio render --live --format wav
readio render --input-format text -- '# Heading is literal text'
```
