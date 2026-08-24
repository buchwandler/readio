---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0004
release_version: v0.1.0
kind: added
summary: Added ingest artifacts, templates, SSMD voice preflight, configuration validation,
  and actionable diagnostics
status: accepted
audience: null
scopes: []
source_refs:
- git:227434fa5e68824906d744a3de162c59dd9fdbea
paths:
- readio/cli.py
- readio/config.py
- readio/document.py
- readio/errors.py
- readio/ingest.py
- readio/paths.py
- readio/ssmd.py
- readio/ssmd_authoring.py
- readio/templates.py
- readio/resources/templates/briefing.ssmd
- readio/resources/templates/dialogue.ssmd
- readio/resources/templates/podcast.ssmd
- tests/test_config.py
- tests/test_doctor.py
- tests/test_document.py
- tests/test_ingest.py
- tests/test_package_resources.py
- tests/test_reader_ssmd.py
- tests/test_ssmd_cli.py
- tests/test_ssmd_integration.py
- tests/test_ssmd_preflight.py
- tests/test_template_validate.py
- tests/test_templates.py
issues: []
prs: []
sources:
- git:227434fa5e68824906d744a3de162c59dd9fdbea
contributors: []
breaking: false
internal: false
order: 4
---
