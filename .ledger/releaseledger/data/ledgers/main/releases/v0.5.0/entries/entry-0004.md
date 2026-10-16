---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 3
entry_id: entry-0004
release_version: v0.5.0
kind: changed
summary: Raised specialist API floors and removed backend-specific Readio extras
status: accepted
audience: null
scopes: []
source_refs:
  - tl:task-0003
paths:
  - pyproject.toml
  - .github/workflows/tests.yml
  - README.md
  - docs/architecture.md
issues: []
prs: []
sources:
  - tl:task-0086
contributors: []
breaking: false
internal: false
order: 4
---

Readio now requires VoiceRender >=0.2.0,<0.3, AudioCompose >=0.3.0,<0.4, and AudioExport >=0.1.2,<0.2. Engine-specific runtime dependencies are installed through VoiceRender extras such as voicerender[kokoro]. Readio retains only dependencies required by its orchestration, direct speech arrays, and local playback.
