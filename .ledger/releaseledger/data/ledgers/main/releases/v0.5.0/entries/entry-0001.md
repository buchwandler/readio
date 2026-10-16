---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0001
release_version: v0.5.0
kind: added
summary: Added direct speech and audiobook orchestration through specialist APIs
status: accepted
audience: null
scopes: []
source_refs:
  - tl:task-0003
paths:
  - readio/speech.py
  - readio/book.py
  - readio/api/__init__.py
  - readio/api/book.py
  - readio/api/speech.py
  - README.md
  - docs/api.md
  - docs/architecture.md
issues: []
prs: []
sources: []
contributors: []
breaking: false
internal: false
order: 1
---

Readio exposes speak, convert_book, open_book, and build_book as a small convenience facade. Direct speech delegates resolution and synthesis to VoiceRender before generic playback. Audiobook build forwards specialist stage results and artifacts without recreating their APIs.
