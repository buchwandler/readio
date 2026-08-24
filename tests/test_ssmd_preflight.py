from pathlib import Path

import pytest

from readio.config import ReadioConfig, VoiceProviderSettings
from readio.errors import SSMDInputError, VoiceResolutionError
from readio.ssmd import (
    build_ssmd_render_config,
    default_role_bindings,
    document_voice_bindings,
    preflight_ssmd,
)


def config() -> ReadioConfig:
    return ReadioConfig(
        voices={
            "kokoro": VoiceProviderSettings(
                ids=("af_sarah", "af_bella", "am_michael"),
                roles={"host": "af_sarah", "analyst": "am_michael", "guest": "af_bella"},
            )
        }
    )


def test_document_binding_overrides_default_and_only_missing_defaults_are_api_bindings():
    text = (
        '---\nvoice_bindings:\n  kokoro:\n    host: af_bella\n---\n<div voice="host">Hello.</div>'
    )

    assert document_voice_bindings(text) == {"kokoro": {"host": "af_bella"}}
    assert default_role_bindings(text, config()) == {
        "kokoro": {"analyst": "am_michael", "guest": "af_bella"}
    }
    render_config = build_ssmd_render_config(text, config())
    assert dict(render_config.voice_bindings["kokoro"]) == {
        "analyst": "am_michael",
        "guest": "af_bella",
    }
    result = preflight_ssmd(text, config())
    assert result.document_bindings == {"host": "af_bella"}
    assert result.default_bindings == {"analyst": "am_michael", "guest": "af_bella"}


@pytest.mark.parametrize(
    "body", ['<div voice="host">Hello.</div>', '<div voice="host">\nHello.\n</div>']
)
def test_single_line_and_multiline_roles_resolve(body: str):
    result = preflight_ssmd(body, config())
    assert result.ok
    assert result.unresolved_references == ()


def test_mixed_document_and_config_bindings_resolve():
    text = '---\nvoice_bindings:\n  kokoro:\n    guest: af_bella\n---\n<div voice="host">Hello.</div>\n<div voice="guest">Hi.</div>'
    result = preflight_ssmd(text, config())
    assert result.ok
    assert result.document_bindings == {"guest": "af_bella"}
    assert result.default_bindings["host"] == "af_sarah"


def test_direct_voice_id_resolves():
    assert preflight_ssmd('<div voice="af_sarah">Hello.</div>', config()).ok


def test_unknown_role_has_actionable_error_and_source():
    source = Path("episode.ssmd")
    with pytest.raises(
        VoiceResolutionError, match="Configure voices.kokoro.roles.unknown_role"
    ) as error:
        preflight_ssmd('<div voice="unknown_role">Hello.</div>', config(), source_path=source)
    assert error.value.code == "ssmd.unresolved_voice_role"
    assert error.value.provider == "kokoro"
    assert error.value.reference == "unknown_role"
    assert error.value.source_path == source


def test_malformed_bindings_are_actionable():
    with pytest.raises(SSMDInputError, match="voice_bindings.kokoro must be a mapping"):
        document_voice_bindings("---\nvoice_bindings:\n  kokoro: bad\n---\nHello")


def test_captured_multi_role_summary_shape_passes():
    blocks = [
        '<div voice="host">Opening.</div>',
        '<div voice="analyst">Context.</div>',
        '<div voice="guest">Question.</div>',
        '<div voice="analyst">Analysis.</div>',
        '<div voice="host">Closing.</div>',
    ]
    result = preflight_ssmd("\n\n".join(blocks), config())
    assert result.ok


def test_preflight_does_not_mutate_source(tmp_path: Path):
    source = tmp_path / "episode.ssmd"
    original = '<div voice="host">Hello.</div>\n'
    source.write_text(original, encoding="utf-8")
    preflight_ssmd(source.read_text(encoding="utf-8"), config(), source_path=source)
    assert source.read_text(encoding="utf-8") == original
