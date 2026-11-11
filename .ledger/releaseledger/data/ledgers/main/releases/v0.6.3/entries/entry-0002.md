---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0002
release_version: v0.6.3
kind: changed
summary:
  Raised minimum VoiceRender to 0.2.11 and AudioCompose to 0.3.10 so VoiceRender
  preserves raw speech and AudioCompose owns terminal-silence conditioning
status: accepted
audience: null
scopes: []
source_refs:
  - tl:task-0016
paths:
  - pyproject.toml
  - .github/workflows/tests.yml
  - .github/workflows/audio-e2e.yml
  - tests/test_packaging_metadata.py
issues: []
prs: []
sources: []
contributors: []
breaking: false
internal: false
order: 2
---
