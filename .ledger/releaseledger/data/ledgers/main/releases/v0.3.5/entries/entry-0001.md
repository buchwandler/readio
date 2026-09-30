---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0001
release_version: v0.3.5
kind: added
summary: Added use_saved_settings for fresh projects.resolve_synthesis() candidates
status: accepted
audience: null
scopes: []
source_refs:
  - tl:task-0062
paths:
  - readio/api/projects.py
  - docs/api.md
issues: []
prs: []
sources: []
contributors: []
breaking: false
internal: false
order: 1
---

The keyword defaults to true for compatibility. Fresh resolution skips persisted project synthesis preferences while retaining normal Readio configuration, language defaults, and call-scoped voice bindings. It still validates the full selection and does not render or mutate the project.
