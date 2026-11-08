---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0002
release_version: v0.6.1
kind: changed
summary:
  Changed intent inspection and rendering to share the canonical RenderRequest
  workflow
status: accepted
audience: null
scopes: []
source_refs: []
paths:
  - readio/api/service.py
  - readio/render_service.py
issues: []
prs: []
sources:
  - tl:task-0013
contributors: []
breaking: false
internal: false
order: 2
---

Intent convenience methods now delegate to Readio.inspect and Readio.render, preserving the canonical persistent-workspace and book-inventory behavior instead of running a separate stage sequence.
