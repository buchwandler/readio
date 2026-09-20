---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0002
release_version: v0.2.5
kind: changed
summary: Requires OnnxVoice 0.1.8+ and exposes additive stable en-US Kokoro selectors
  through voices list --engine kokoro --lang en-us without changing de-ko-* identities
status: accepted
audience: null
scopes: []
source_refs:
- tl:task-0036
paths:
- pyproject.toml
- readio/voices.py
- tests/test_voice_catalog.py
- tests/test_voices_cli.py
issues: []
prs: []
sources: []
contributors: []
breaking: false
internal: false
order: 2
---
