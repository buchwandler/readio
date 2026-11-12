---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.6.4
kind: changed
summary: Changed dependency floors to VoiceRender 0.2.11 and AudioCompose 0.3.10
status: accepted
audience: null
scopes: []
source_refs:
  - git:b2831ffe4ef5d4539fb03ec98c2c02bfd264cb2c
  - git:b9e19a401be683e733c150623a9d1a011f6f8f74
  - tl:task-0016
paths:
  - .github/workflows/audio-e2e.yml
  - .github/workflows/tests.yml
  - pyproject.toml
  - tests/test_dependency_boundaries.py
  - tests/test_packaging_metadata.py
  - tests/test_render_workflow_persistent.py
  - tests/test_speech.py
issues: []
prs: []
sources: []
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---

These minimum versions preserve raw speech for composition and keep terminal-silence conditioning in AudioCompose.
