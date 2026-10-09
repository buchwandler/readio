---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0002
release_version: v0.5.0
kind: removed
summary: Removed Readio-owned SSMD authoring and template workflows
status: accepted
audience: null
scopes: []
source_refs:
  - tl:task-0085
paths:
  - readio/cli.py
  - readio/api/app.py
  - readio/config.py
  - README.md
issues: []
prs: []
sources: []
contributors: []
breaking: true
internal: false
order: 2
---

Template and draft management, authoring APIs, binding materialization, and authoring-only SSMD lint are no longer part of Readio. Use SSMDStudio 0.1.1 to author, bind, and structurally validate portable SSMD files. Readio retains SSMD parsing, consumer checks, planning, and rendering. Legacy authoring configuration keys warn and are ignored; new writes omit them, and existing files and directories are left untouched.
