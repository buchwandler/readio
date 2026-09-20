---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0001
release_version: v0.2.5
kind: changed
summary:
  Changed Readio's runtime integration for fixed timestamp-capable releases
  and OnnxVoice diagnostics
status: accepted
audience: null
scopes: []
source_refs:
  - tl:task-0035
paths:
  - pyproject.toml
  - readio/logging_config.py
  - tests/test_engine_spine.py
issues: []
prs: []
sources: []
contributors: []
breaking: false
internal: false
order: 1
---

Raised the OnnxVoice and PyKokoro runtime floors, exposed OnnxVoice records through -vv, and added an active-engine regression guard for explicit short-sentence policies.
