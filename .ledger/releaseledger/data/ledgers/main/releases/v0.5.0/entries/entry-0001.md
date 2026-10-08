---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.5.0
kind: added
summary: Added optional InflectSynth synthesis via the canonical inflect engine ID
status: accepted
audience: null
scopes: []
source_refs: []
paths:
  - pyproject.toml
  - readio/engines/inflectsynth.py
  - README.md
  - docs/index.md
  - docs/api.md
  - docs/cli.md
  - docs/architecture.md
issues: []
prs: []
sources: []
contributors: []
breaking: false
internal: false
order: 1
---

Install the optional engine with readio[inflect]. Public metadata discovery currently supplies nano-v2 and micro-v2, each with the fixed default voice; synthesis is English-only and supports speed, variation, seed, and calibrated voice level. Speaker selection, reference voices, pronunciation overrides, and native word timings are unsupported. InflectSynth 0.1.1 does not publish a trustworthy capacity maximum, so Readio treats capacity as unknown rather than guessing a token limit. Readio owns request boundaries; InflectSynth owns G2P and model/runtime internals, with ONNXVoice remaining transitive.
