---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.6.1
kind: added
summary: Added read-only source inspection and saved-intent conversion into RenderRequest
status: accepted
audience: null
scopes: []
source_refs:
  - tl:task-0013
paths:
  - readio/api/service.py
  - readio/api/types.py
  - readio/integrations/ssmdconvert.py
  - docs/api.md
issues: []
prs: []
sources: []
contributors: []
breaking: false
internal: false
order: 1
---

Source inspection exposes normalized metadata and book chapter inventory without creating a workspace. Persisted render intents can now be resolved through a public bridge to the canonical RenderRequest API.
