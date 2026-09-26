---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.3.1
kind: added
summary:
  Added audiobook chapter descriptions and non-rendering synthesis resolution
  to the public API
status: accepted
audience: null
scopes: []
source_refs:
  - git:86a3aecedd9d31f8c36e2b85d8ec18aa833a2472
paths:
  - docs/api.md
  - readio/api/__init__.py
  - readio/api/audiobooks.py
  - readio/api/projects.py
  - readio/api/types.py
  - readio/stages/synthesis.py
  - tests/test_public_api_imports.py
  - tests/test_public_api_projects.py
  - tests/test_public_api_types.py
issues: []
prs: []
sources:
  - git:86a3aecedd9d31f8c36e2b85d8ec18aa833a2472
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---
