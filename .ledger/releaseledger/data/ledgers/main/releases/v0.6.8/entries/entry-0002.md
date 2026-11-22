---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0002
release_version: v0.6.8
kind: added
summary:
  Added versioned JSON contracts, read-only capability discovery, and an optional
  MCP server for guarded workflow tools
status: accepted
audience: null
scopes: []
source_refs:
  - git:7da1a441b8fee579424c3c64607878902412feb0
paths:
  - docs/api.md
  - docs/architecture.md
  - docs/cli.md
  - pyproject.toml
  - readio/api/serialization.py
  - readio/capabilities.py
  - readio/mcp/server.py
  - readio/render_workflow.py
  - readio/workflow.py
  - tests/test_capabilities.py
  - tests/test_mcp_adapter.py
  - tests/test_public_serialization_contract.py
issues: []
prs: []
sources:
  - git:7da1a441b8fee579424c3c64607878902412feb0
contributors:
  - "@holgern"
breaking: false
internal: false
order: 2
---
