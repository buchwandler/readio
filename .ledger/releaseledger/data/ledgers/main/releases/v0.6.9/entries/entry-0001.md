---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.6.9
kind: added
summary:
  Added normalized engine-model inventory and install, verify, and remove APIs
  with CLI and versioned JSON
status: accepted
audience: null
scopes: []
source_refs:
  - git:ce88b6fa0628d3ee0a24a28fa20b4558a32edd9a
paths:
  - README.md
  - docs/api.md
  - docs/cli.md
  - readio/api/__init__.py
  - readio/api/serialization.py
  - readio/api/service.py
  - readio/api/types.py
  - readio/cli_control.py
  - readio/errors.py
  - readio/model_catalog.py
  - tests/test_cli_control.py
  - tests/test_model_catalog.py
  - tests/test_model_management.py
  - tests/test_public_package_api_contract.py
  - tests/test_public_serialization_contract.py
issues: []
prs: []
sources: []
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---

Model updates remain unsupported until VoiceRender exposes authoritative update semantics.
