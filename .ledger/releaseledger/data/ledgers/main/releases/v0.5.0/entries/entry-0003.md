---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0003
release_version: v0.5.0
kind: added
summary: Added optional AudioExport profile exports for project audio and M4B
status: accepted
audience: null
scopes: []
source_refs:
  - tl:task-0086
paths:
  - pyproject.toml
  - readio/cli.py
  - readio/project_settings.py
  - readio/stages/export.py
  - readio/stages/audiobook_export.py
  - readio/stages/pipeline.py
  - README.md
  - docs/cli.md
  - docs/projects.md
  - docs/api.md
issues: []
prs: []
sources: []
contributors: []
breaking: false
internal: false
order: 3
---

Generic and audiobook file exports can opt into AudioExport profiles; saved profiles participate in build and status, while profile-only changes stale only the output stage. Readio retains output ownership, state, and verified audiobook timeline checks. The dependency remains optional, and profile-free exports plus speak streaming keep their existing paths.
