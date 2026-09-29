---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0004
release_version: v0.3.4
kind: added
summary:
  Added saved project settings for synthesis, composition, export, and audiobook
  workflows with stage-aware rebuilds
status: accepted
audience: null
scopes: []
source_refs:
  - git:c9c1f8c0159fe33746a96d12c48ca2f09749c54a
paths:
  - README.md
  - docs/api.md
  - docs/cli.md
  - docs/projects.md
  - readio/api/__init__.py
  - readio/api/audiobooks.py
  - readio/api/projects.py
  - readio/api/types.py
  - readio/cli.py
  - readio/project_model.py
  - readio/project_settings.py
  - readio/stages/audiobook_export.py
  - readio/stages/pipeline.py
  - readio/stages/planning.py
  - readio/stages/synthesis.py
  - tests/test_audiobook_export_api.py
  - tests/test_audiobook_status.py
  - tests/test_cli.py
  - tests/test_project_model.py
  - tests/test_public_api_imports.py
  - tests/test_public_api_projects.py
  - tests/test_public_api_types.py
  - tests/typecheck/public_api_consumer.py
issues: []
prs: []
sources:
  - git:c9c1f8c0159fe33746a96d12c48ca2f09749c54a
contributors:
  - "@holgern"
breaking: false
internal: false
order: 4
---
