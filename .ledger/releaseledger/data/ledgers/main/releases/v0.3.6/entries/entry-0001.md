---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.3.6
kind: changed
summary:
  Changed voice selection to use target-qualified semantic references instead
  of numbered selectors
status: accepted
audience: null
scopes: []
source_refs:
  - git:4a8270fecb1de75b87595063ac59e4f2acc2f473
paths:
  - README.md
  - docs/api.md
  - docs/architecture.md
  - docs/cli.md
  - docs/index.md
  - docs/projects.md
  - readio/api/catalog.py
  - readio/api/configuration.py
  - readio/api/roles.py
  - readio/api/types.py
  - readio/cli.py
  - readio/config.py
  - readio/engines/pipersynth.py
  - readio/plan.py
  - readio/project_model.py
  - readio/project_roles.py
  - readio/role_targets.py
  - readio/spotify_cli.py
  - readio/ssmd.py
  - readio/stages/synthesis.py
  - readio/synthesis.py
  - readio/voice_refs.py
  - readio/voices.py
  - skill/readio/SKILL.md
  - skill/readio/references/cli.md
  - skill/readio/references/ssmd.md
  - skill/readio/references/troubleshooting.md
  - tests/test_config.py
  - tests/test_project_roles.py
  - tests/test_public_api_catalog_listings.py
  - tests/test_public_api_catalog_roles_ssmd_extensions.py
  - tests/test_public_api_configuration_templates_ingest_diagnostics.py
  - tests/test_public_api_projects.py
  - tests/test_pykokoro_10_regressions.py
  - tests/test_role_targets.py
  - tests/test_voice_api.py
  - tests/test_voice_catalog.py
  - tests/test_voice_refs.py
  - tests/test_voice_resolution.py
  - tests/test_voices_cli.py
issues: []
prs: []
sources:
  - git:4a8270fecb1de75b87595063ac59e4f2acc2f473
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---

Voice references use SYSTEM:TARGET[/VOICE], for example kokoro:v1.0/af_heart. Use them across voice discovery, synthesis requests, and role bindings; native voice IDs remain supported when engine and target context resolve them uniquely.
