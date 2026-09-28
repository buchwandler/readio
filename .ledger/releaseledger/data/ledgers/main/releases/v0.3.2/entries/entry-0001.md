---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.3.2
kind: changed
summary:
  Changed the default short-sentence policy to phrase, with migration for legacy
  auto settings
status: accepted
audience: null
scopes: []
source_refs:
  - git:0251443b706966ad97a118f3494246f20e005001
paths:
  - docs/index.md
  - readio/config.py
  - readio/engines/pykokoro.py
  - readio/plan.py
  - readio/synthesis.py
  - readio/cli.py
  - readio/spotify_cli.py
  - skill/readio/references/cli.md
  - skill/readio/references/troubleshooting.md
  - tests/test_config.py
  - tests/test_engine_spine.py
  - tests/test_plan.py
  - tests/test_spotify_cli.py
issues: []
prs: []
sources:
  - git:0251443b706966ad97a118f3494246f20e005001
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---
