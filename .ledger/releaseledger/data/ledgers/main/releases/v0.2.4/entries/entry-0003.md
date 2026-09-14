---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0003
release_version: v0.2.4
kind: added
summary:
  Added backend-neutral lexicon discovery and CLI inspection with language,
  model, and engine filters
status: accepted
audience: null
scopes: []
source_refs:
  - git:5840ad53704ab4efd000346c048638c9b2271eb7
paths:
  - README.md
  - docs/index.md
  - readio/audio.py
  - readio/backends/__init__.py
  - readio/backends/base.py
  - readio/backends/pykokoro.py
  - readio/backends/registry.py
  - readio/cli.py
  - readio/config.py
  - readio/lexicons.py
  - readio/models.py
  - readio/plan.py
  - readio/reader.py
  - readio/ssmd.py
  - readio/synthesis.py
  - readio/voices.py
  - skill/readio/SKILL.md
  - skill/readio/references/cli.md
  - tests/test_backend_config.py
  - tests/test_backend_dispatch.py
  - tests/test_backends.py
  - tests/test_lexicon_catalog.py
  - tests/test_lexicons_cli.py
issues: []
prs: []
sources:
  - git:5840ad53704ab4efd000346c048638c9b2271eb7
contributors:
  - "@holgern"
breaking: false
internal: false
order: 3
---
