# Input formats

Readio has three source paths:

- `readio speak` accepts literal text as positional arguments. It does not read files or interpret Markdown/SSMD structure. Use `readio.api.speak(text)` when text is already available in Python.
- `readio render SOURCE --profile PROFILE` sends one supported document to SSMDConvert, then plans, synthesizes, composes, and optionally exports it. SSMDConvert owns supported formats and optional parser dependencies; consult its documentation for the installed formats.
- `readio audiobook convert SOURCE` converts an EPUB into an SSMDConvert-owned `.ssmdbook` workspace. `readio audiobook build WORKSPACE` consumes that workspace and a VoiceRender RenderProfile.

Do not pass Markdown or SSMD to `speak` expecting a reader pipeline. For multi-role or chapter-based book content, use SSMDConvert conversion and the audiobook/specialist workflow.

```bash
readio speak "A short literal sentence."
readio render chapter.docx --profile narrator.renderprofile.toml --output chapter.m4a
readio audiobook convert novel.epub --output novel.ssmdbook
readio audiobook build novel.ssmdbook --profile narrator.renderprofile.toml --output novel.m4b
```
