# Input formats

Readio exposes three source paths:

- `readio speak` accepts literal text as positional arguments. It does not read files or interpret Markdown/SSMD. Use `readio.api.speak(text)` when text is already available in Python.
- `readio init SOURCE` converts a supported source into an SSMDConvert-owned `.ssmdoc` or `.ssmdbook`. EPUB is inferred as a book; other supported formats are documents. SSMDConvert owns the supported formats and optional parser dependencies.
- Pipeline commands accept an existing native `.ssmdoc` or `.ssmdbook` workspace. Use `readio plan`, `readio render-plan`, or `readio run` to dispatch specialist stages.

Do not pass Markdown or SSMD to `speak` expecting a reader pipeline. For structured multi-role or chapter-based content, create a native workspace and use UtterPlan and the downstream specialist workflow.

```bash
readio speak "A short literal sentence."
readio init article.docx -o article.ssmdoc
readio init novel.epub -o novel.ssmdbook
readio run article.ssmdoc --profile narrator.renderprofile.toml --language en-US
```
