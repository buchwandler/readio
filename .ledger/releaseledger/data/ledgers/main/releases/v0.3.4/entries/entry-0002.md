---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0002
release_version: v0.3.4
kind: added
summary:
  Added mastering profiles with LUFS and true-peak reporting across composition
  workflows
status: accepted
audience: null
scopes: []
source_refs:
  - git:ca9684a23eee5da638ddfe80efd891429ec53911
paths:
  - docs/api.md
  - docs/cli.md
  - docs/projects.md
  - readio/api/__init__.py
  - readio/api/projects.py
  - readio/api/speech.py
  - readio/api/types.py
  - readio/cli.py
  - readio/cli_adapter.py
  - readio/execution.py
  - readio/plan.py
  - readio/stages/composition.py
  - readio/stages/pipeline.py
  - tests/test_cli.py
  - tests/test_incremental_project_render.py
  - tests/test_mastering_profiles.py
  - tests/test_project_composition.py
  - tests/test_public_api_events.py
  - tests/test_public_api_projects.py
  - tests/test_render_from_plan.py
issues: []
prs: []
sources:
  - git:ca9684a23eee5da638ddfe80efd891429ec53911
contributors:
  - "@holgern"
breaking: false
internal: false
order: 2
---
