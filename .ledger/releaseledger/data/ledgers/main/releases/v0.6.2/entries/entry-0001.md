---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.6.2
kind: added
summary:
  Added configurable speech preparation and pause timing, versioned render
  intents, and explicit spaCy model management
status: accepted
audience: null
scopes: []
source_refs:
  - git:1edab537d2112716deeeaecfe13911a344acdaa3
paths:
  - .github/workflows/tests.yml
  - README.md
  - docs/api.md
  - pyproject.toml
  - readio/api/__init__.py
  - readio/api/service.py
  - readio/api/types.py
  - readio/integrations/utterplan.py
  - readio/intent.py
  - readio/intent_codec.py
  - readio/intent_resolver.py
  - readio/render_service.py
  - readio/render_workflow.py
  - readio/spacy_resources.py
  - readio/workspace.py
  - tests/test_dependency_boundaries.py
  - tests/test_intent.py
  - tests/test_intent_cli.py
  - tests/test_intent_request_bridge.py
  - tests/test_native_inspection_dispatch.py
  - tests/test_native_utterplan_dispatch.py
  - tests/test_native_workspace.py
  - tests/test_native_workspace_e2e.py
  - tests/test_packaging_metadata.py
  - tests/test_public_package_api_contract.py
  - tests/test_render_workflow.py
  - tests/test_spacy_resources.py
issues: []
prs: []
sources:
  - git:1edab537d2112716deeeaecfe13911a344acdaa3
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---
