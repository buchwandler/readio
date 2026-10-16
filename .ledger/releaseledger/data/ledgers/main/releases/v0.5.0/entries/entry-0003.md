---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0003
release_version: v0.5.0
kind: changed
summary: Delegated audiobook stages to specialist-owned public APIs
status: accepted
audience: null
scopes: []
source_refs:
  - tl:task-0003
paths:
  - readio/book.py
  - README.md
  - docs/architecture.md
  - docs/api.md
  - docs/cli.md
issues: []
prs: []
sources: []
contributors: []
breaking: false
internal: false
order: 3
---

Readio calls SSMDConvert for conversion and workspace access, UtterPlan for semantic compilation and RenderPlan projection, VoiceRender for synthesis, AudioCompose for composition, and AudioExport for export. It passes the exact UtterPlan variant through later stages and returns the original specialist result objects. Specialist artifacts, reports, validation, and reuse rules remain authoritative.
