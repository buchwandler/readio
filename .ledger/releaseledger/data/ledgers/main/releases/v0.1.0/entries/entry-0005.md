---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0005
release_version: v0.1.0
kind: quality
summary:
  Added cross-platform CI, pre-commit, coverage, package publishing checks,
  and a podcast example
status: accepted
audience: null
scopes: []
source_refs:
  - git:1cdb326b8e406caac13d352fdccf1c06dffd898b
paths:
  - .github/workflows/codecov.yml
  - .github/workflows/pre-commit.yml
  - .github/workflows/python-publish.yml
  - .github/workflows/tests.yml
  - examples/README.md
  - examples/readio-podcast.ssmd
  - tests/test_document.py
  - tests/test_ssmd_cli.py
  - tests/test_ssmd_integration.py
  - tests/test_ssmd_preflight.py
issues: []
prs: []
sources:
  - git:1cdb326b8e406caac13d352fdccf1c06dffd898b
contributors: []
breaking: false
internal: false
order: 5
---
