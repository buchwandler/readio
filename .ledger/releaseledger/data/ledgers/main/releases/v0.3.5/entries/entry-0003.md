---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0003
release_version: v0.3.5
kind: changed
summary:
  Changed Piper and Pocket gender projection to preserve explicit metadata
  without inferring missing values
status: accepted
audience: null
scopes: []
source_refs: []
paths:
  - readio/engines/pipersynth.py
  - readio/engines/pocketsynth.py
  - readio/api/catalog.py
issues: []
prs: []
sources:
  - tl:task-0062
contributors: []
breaking: false
internal: false
order: 3
---

Gender is retained only when supplied by supported engine metadata. Missing values remain unknown; voice names and identifiers are not used to infer gender.
