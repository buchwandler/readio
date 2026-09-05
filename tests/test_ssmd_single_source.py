"""Table-driven single-source test for SSMD voice-binding resolution.

The same SSMD input is fed through every view that consumes bindings:

    resolve_voice_references   (the shared primitive)
    resolve_plan               (SSMDPlan.bindings)
    analyze_ssmd / preflight   (preflight payload)
    build_ssmd_render_config   (runtime SSMDRenderConfig)

and every view must agree on the effective reference -> voice mapping.
"""

from __future__ import annotations

import pytest

from readio.config import ReadioConfig
from readio.document import InputDocument
from readio.plan import (
    InputRequest,
    OutputRequest,
    PlanRequest,
    resolve_plan,
)
from readio.ssmd import analyze_ssmd, build_ssmd_render_config, resolve_voice_references

DOCUMENT_BOUND = """---
voice_bindings:
  kokoro:
    host: af_bella
---
<div voice="host">Document wins.</div>
"""


def _ssmd_request(text: str, *, bindings: dict[str, str] | None = None) -> PlanRequest:
    return PlanRequest(
        operation="render",
        input=InputRequest(
            document=InputDocument(text=text, source_path=None, format="ssmd"),
        ),
        output=OutputRequest(),
        voice_bindings=bindings or {},
    )


CASES = [
    pytest.param(
        DOCUMENT_BOUND,
        None,
        {"host": ("af_bella", "document")},
        id="document-binding",
    ),
    pytest.param(
        '<div voice="host">Invocation wins over role.</div>',
        {"host": "af_bella"},
        {"host": ("af_bella", "cli")},
        id="invocation-binding",
    ),
    pytest.param(
        '<div voice="host">Configured role.</div>',
        None,
        {"host": ("af_sarah", "config.voice_role")},
        id="configured-role",
    ),
    pytest.param(
        '<div voice="af_sarah">Direct voice reference.</div>',
        None,
        {"af_sarah": ("af_sarah", "direct")},
        id="direct-voice",
    ),
    pytest.param(
        DOCUMENT_BOUND,
        {"host": "am_michael"},
        {"host": ("af_bella", "document")},
        id="precedence-document-over-invocation-and-role",
    ),
    pytest.param(
        '<div voice="host">Invocation beats the configured role.</div>',
        {"host": "af_bella"},
        {"host": ("af_bella", "cli")},
        id="precedence-invocation-over-role",
    ),
]


@pytest.mark.parametrize(
    ("name", "text", "bindings", "expected"),
    [(case.id, *case.values) for case in CASES],
)
def test_ssmd_binding_views_agree(name, text, bindings, expected) -> None:
    cfg = ReadioConfig()
    expected_voices = {ref: voice for ref, (voice, _origin) in expected.items()}

    # View 1: the shared primitive
    resolved = resolve_voice_references(text, cfg, additional_bindings=bindings)
    primitive = {item.reference: (item.voice, item.origin) for item in resolved}
    assert primitive == expected

    # View 2: the plan
    plan = resolve_plan(cfg, _ssmd_request(text, bindings=bindings))
    assert plan.ok, [d.message for d in plan.diagnostics]
    plan_view = {b.reference: (b.voice, b.origin) for b in plan.ssmd.bindings}
    assert plan_view == expected

    # View 3: preflight
    analysis = analyze_ssmd(text, cfg, additional_bindings=bindings)
    assert analysis.unresolved_references == ()

    def preflight_target(reference: str) -> str:
        if reference in analysis.document_bindings:
            return analysis.document_bindings[reference]
        if reference in analysis.runtime_bindings:
            return analysis.runtime_bindings[reference]
        if reference in analysis.default_bindings:
            return analysis.default_bindings[reference]
        return reference  # direct voice reference

    assert {ref: preflight_target(ref) for ref in expected_voices} == expected_voices

    # View 4: runtime SSMDRenderConfig bindings
    render_config = build_ssmd_render_config(text, cfg, additional_bindings=bindings)
    runtime_map = dict(render_config.voice_bindings.get("kokoro", {}))
    # Document-local bindings live in the document itself; the runtime map
    # Document-local bindings live in the document itself; direct references
    # are already concrete voice IDs the pipeline resolves natively. The
    # runtime map carries everything else.
    runtime_expected = {
        ref: voice
        for ref, (voice, origin) in expected.items()
        if origin not in ("document", "direct")
    }
    assert {ref: voice for ref, voice in runtime_map.items() if ref in expected_voices} == (
        runtime_expected
    )


def test_unresolved_reference_is_unresolved_in_every_view() -> None:
    cfg = ReadioConfig()
    text = '<div voice="unknown_role">Nobody bound this role.</div>'

    resolved = resolve_voice_references(text, cfg)
    assert [item.reference for item in resolved if item.voice is None] == ["unknown_role"]

    plan = resolve_plan(cfg, _ssmd_request(text))
    assert not plan.ok
    assert plan.ssmd.unresolved == ("unknown_role",)
    codes = {d.code for d in plan.diagnostics}
    assert "ssmd_unresolved_voice" in codes

    analysis = analyze_ssmd(text, cfg)
    assert analysis.unresolved_references == ("unknown_role",)
