---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0004
release_version: v0.2.4
kind: fixed
summary:
  Fixed lexicon listing without a language filter while preserving backend-aware
  selector disambiguation
status: accepted
audience: null
scopes: []
source_refs:
  - git:2c5e221eab0f4047a8157dcacb85e5d31c956d3a
paths:
  - README.md
  - docs/index.md
  - readio/audio.py
  - readio/backends/base.py
  - readio/backends/pykokoro.py
  - readio/cli.py
  - readio/lexicons.py
  - readio/models.py
  - readio/plan.py
  - readio/reader.py
  - readio/ssmd.py
  - readio/synthesis.py
  - readio/voices.py
  - skill/readio/SKILL.md
  - skill/readio/references/cli.md
  - tests/test_backend_dispatch.py
  - tests/test_lexicons_cli.py
  - tests/test_voice_catalog.py
  - tests/test_voices_cli.py
issues: []
prs: []
sources:
  - git:2c5e221eab0f4047a8157dcacb85e5d31c956d3a
contributors:
  - "@holgern"
breaking: false
internal: false
order: 4
---
