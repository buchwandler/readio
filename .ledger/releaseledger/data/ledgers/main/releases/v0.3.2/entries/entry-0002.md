---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0002
release_version: v0.3.2
kind: changed
summary:
  Changed project status output with clear issue explanations and actionable
  commands in the CLI and public API
status: accepted
audience: null
scopes: []
source_refs: []
paths:
  - readio/api/projects.py
  - readio/api/types.py
  - readio/cli.py
  - readio/stages/pipeline.py
  - tests/test_cli.py
  - tests/test_incremental_project_render.py
  - tests/test_public_api_projects.py
issues: []
prs: []
sources:
  - git:0251443b706966ad97a118f3494246f20e005001
contributors:
  - "@holgern"
breaking: false
internal: false
order: 2
---
