---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 4
entry_id: entry-0002
release_version: v0.5.0
kind: removed
summary: Removed legacy Readio project formats without a migration path
status: accepted
audience: null
scopes: []
source_refs:
  - tl:task-0003
paths:
  - readio/cli.py
  - readio/book.py
  - pyproject.toml
  - README.md
  - docs/api.md
  - docs/cli.md
  - docs/architecture.md
issues: []
prs: []
sources: []
contributors: []
breaking: true
internal: false
order: 2
---

**BREAKING.** Generic Readio projects, `.readio` state, `project.json`, old plan and render-manifest formats, project migrations, the Readio engine and stage pipeline, and the mirrored project/runtime APIs and CLI commands are removed. There is no migration command or runtime compatibility path. Start from source material and convert it to an SSMDConvert-owned `.ssmdbook` workspace, or use the specialist packages directly.
