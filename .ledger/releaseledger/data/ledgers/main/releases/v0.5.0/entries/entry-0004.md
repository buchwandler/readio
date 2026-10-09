---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0004
release_version: v0.5.0
kind: changed
summary: Changed profile mode to leave legacy exports and speak streaming unchanged
status: accepted
audience: null
scopes: []
source_refs: []
paths:
  - readio/stages/export.py
  - readio/wave.py
  - readio/cli.py
  - README.md
  - docs/cli.md
issues: []
prs: []
sources:
  - tl:task-0086
contributors: []
breaking: false
internal: false
order: 4
---

AudioExport remains an opt-in file-export backend. Existing profile-free project exports retain the legacy implementation, and speak continues to stream PCM through Readio's existing sink.
