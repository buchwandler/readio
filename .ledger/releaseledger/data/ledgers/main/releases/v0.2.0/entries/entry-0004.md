---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0004
release_version: v0.2.0
kind: changed
summary:
  Changed model-source selection and model-scoped voice resolution to apply
  consistently across synthesis and SSMD
status: accepted
audience: null
scopes: []
source_refs:
  - git:f941a49abbc0187d3b8583a055b45e9099a4bd49
paths:
  - readio/cli.py
  - readio/models.py
  - readio/reader.py
  - readio/ssmd.py
  - readio/synthesis.py
  - tests/test_model_source_preference.py
  - tests/test_ssmd_model_roster.py
issues: []
prs: []
sources:
  - git:f941a49abbc0187d3b8583a055b45e9099a4bd49
contributors: []
breaking: false
internal: false
order: 4
---
