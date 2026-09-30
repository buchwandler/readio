---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0004
release_version: v0.3.5
kind: changed
summary:
  Changed voice metadata projection to preserve stable selectors independently
  of descriptive locales
status: accepted
audience: null
scopes: []
source_refs: []
paths:
  - readio/voices.py
  - readio/api/catalog.py
  - README.md
  - docs/api.md
issues: []
prs: []
sources:
  - tl:task-0062
contributors: []
breaking: false
internal: false
order: 4
---

Existing selector identities such as en-pi-13 and Kokoro selectors are unchanged. Selector language remains a stable namespace and is not replaced with a descriptive regional locale.
