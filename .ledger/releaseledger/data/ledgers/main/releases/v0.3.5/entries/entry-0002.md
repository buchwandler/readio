---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0002
release_version: v0.3.5
kind: changed
summary:
  Changed voice language filtering to honor generic capabilities and conflicting
  locales
status: accepted
audience: null
scopes: []
source_refs: []
paths:
  - readio/api/catalog.py
  - readio/engines/pipersynth.py
  - readio/engines/pocketsynth.py
  - readio/voices.py
  - README.md
  - docs/cli.md
issues: []
prs: []
sources:
  - tl:task-0062
contributors: []
breaking: false
internal: false
order: 2
---

Voice language remains a base-language value while locale carries canonical regional metadata. A generic Pocket language such as en matches an en-US query, but an explicit en-GB locale does not; unsupported regional specificity is not inferred.
