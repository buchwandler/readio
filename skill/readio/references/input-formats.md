# Input formats

Readio exposes two source paths:

- `readio speak` accepts literal text as positional arguments. It does not interpret Markdown or SSMD. Use `readio.api.speak(text)` when text is already available in Python.
- `readio render SOURCE --profile PROFILE` delegates conversion and content-kind detection to SSMDConvert, then orchestrates planning, synthesis, composition, and optional export. Accepted formats and parser dependencies are owned by SSMDConvert.

`readio render` accepts source material or an existing `.ssmd-bundle`. The bundle manifest records whether content is a book or document, and specialists perform kind-sensitive dispatch. Intermediate workspace is temporary by default; add `--bundle PATH` when an editable bundle should persist.

Do not pass structured Markdown or SSMD to `speak` expecting a reader pipeline. Use `render` with a VoiceRender profile for structured multi-role or chapter-based content. On first creation of a persistent book bundle, the complete source inventory is materialized; chapter selection limits downstream execution.

```bash
readio speak "A short literal sentence."
readio render article.docx --profile narrator.renderprofile.toml --language en-US
readio render novel.epub --bundle novel.ssmd-bundle \
  --profile narrator.renderprofile.toml --language en-US --chapters 5-17
readio render novel.ssmd-bundle --profile narrator.renderprofile.toml --chapters 2
```
