# Input formats

Readio exposes two source paths:

- `readio speak` accepts literal text as positional arguments. It does not interpret Markdown or SSMD. Use `readio.api.speak(text)` when text is already available in Python.
- `readio bundle convert SOURCE` delegates source conversion to SSMDConvert and produces an editable `.ssmd-bundle`. SSMDConvert owns accepted formats, source-kind detection, and optional parser dependencies.

Bundle lifecycle commands accept a source for conversion or an existing `.ssmd-bundle` directory. The manifest records whether the bundle represents a book or a document, and specialist APIs perform kind-sensitive dispatch.

Do not pass structured Markdown or SSMD to `speak` expecting a reader pipeline. For structured multi-role or chapter-based content, convert the source into a bundle and use UtterPlan and the downstream specialist workflow.

```bash
readio speak "A short literal sentence."
readio bundle convert article.docx -o article.ssmd-bundle
readio bundle convert novel.epub -o novel.ssmd-bundle
readio bundle run article.ssmd-bundle --profile narrator.renderprofile.toml --language en-US
```
