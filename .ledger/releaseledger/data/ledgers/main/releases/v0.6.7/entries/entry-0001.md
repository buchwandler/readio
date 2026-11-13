---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.6.7
kind: fixed
summary:
  Fixed package installation by making AudioExport a required dependency and
  validating wheel metadata during publishing
status: accepted
audience: null
scopes: []
source_refs:
  - git:2eae05631b32a7aad2279fe3e3985928aa201a28
paths:
  - .github/workflows/codecov.yml
  - .github/workflows/python-publish.yml
  - .github/workflows/tests.yml
  - pyproject.toml
  - readio/pipeline.py
  - tests/test_native_workspace.py
  - tests/test_packaging_metadata.py
issues: []
prs: []
sources:
  - git:2eae05631b32a7aad2279fe3e3985928aa201a28
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---
