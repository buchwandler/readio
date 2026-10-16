# Input formats

Readio 0.5 has two input paths:

- `readio speak` accepts literal text as positional arguments. It does not read a file or interpret Markdown/SSMD structure. Use `readio.api.speak(text)` when text is already available as a Python string.
- `readio audiobook convert SOURCE` delegates document conversion and workspace creation to SSMDConvert. EPUB is the documented audiobook path. Consult SSMDConvert for its complete supported source formats.
- `readio audiobook build WORKSPACE` consumes an SSMDConvert-owned `.ssmdbook` workspace. The VoiceRender RenderProfile and each specialist stage's inputs remain package-owned.

Do not pass a Markdown or SSMD file to `speak` expecting the removed Readio reader pipeline to parse it. For role-based, multi-speaker book content, use the specialist workflow described in [SSMD and specialist ownership](ssmd.md).

```bash
readio speak "A short literal sentence."
readio audiobook convert novel.epub --output novel.ssmdbook
readio audiobook build novel.ssmdbook --profile narration.renderprofile.toml --output novel.m4b
```
