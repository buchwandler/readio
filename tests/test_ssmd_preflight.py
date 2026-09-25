from pathlib import Path

import pytest

from readio.config import ReadioConfig, VoiceProviderSettings
from readio.errors import SSMDInputError, VoiceResolutionError
from readio.ssmd import default_role_bindings, document_voice_bindings, preflight_ssmd


def config() -> ReadioConfig:
    return ReadioConfig(
        voices={
            "kokoro": VoiceProviderSettings(
                ids=("af_sarah", "af_bella", "am_michael"),
                roles={"host": "af_sarah", "analyst": "am_michael", "guest": "af_bella"},
            )
        }
    )


def _voice_block(role: str, text: str) -> str:
    return f':::{{voice="{role}"}}\n{text}\n:::'


def test_document_binding_overrides_default_and_only_missing_defaults_are_api_bindings():
    text = (
        "---\nssmd_version: '0.9'\nvoice_bindings:\n  kokoro:\n    host: af_bella\n---\n"
        + _voice_block("host", "Hello.")
    )

    assert document_voice_bindings(text) == {"kokoro": {"host": "af_bella"}}
    assert default_role_bindings(text, config()) == {
        "kokoro": {"analyst": "am_michael", "guest": "af_bella"}
    }
    result = preflight_ssmd(text, config())
    assert result.document_bindings == {"host": "af_bella"}
    assert result.default_bindings == {"analyst": "am_michael", "guest": "af_bella"}


@pytest.mark.parametrize(
    "body",
    ['[Hello.]{voice="host"}', _voice_block("host", "Hello.")],
)
def test_single_line_and_multiline_roles_resolve(body: str):
    result = preflight_ssmd(body, config())
    assert result.ok
    assert result.unresolved_references == ()


def test_mixed_document_and_config_bindings_resolve():
    text = (
        "---\nssmd_version: '0.9'\nvoice_bindings:\n  kokoro:\n    guest: af_bella\n---\n"
        + _voice_block("host", "Hello.")
        + "\n"
        + _voice_block("guest", "Hi.")
    )
    result = preflight_ssmd(text, config())
    assert result.ok
    assert result.document_bindings == {"guest": "af_bella"}
    assert result.default_bindings["host"] == "af_sarah"


def test_direct_voice_id_resolves():
    assert preflight_ssmd(_voice_block("af_sarah", "Hello."), config()).ok


def test_unknown_role_has_actionable_error_and_source():
    source = Path("episode.ssmd")
    with pytest.raises(
        VoiceResolutionError, match="Configure voices.kokoro.roles.unknown_role"
    ) as error:
        preflight_ssmd(_voice_block("unknown_role", "Hello."), config(), source_path=source)
    assert error.value.code == "ssmd.unresolved_voice_role"
    assert error.value.provider == "kokoro"
    assert error.value.reference == "unknown_role"
    assert error.value.source_path == source


def test_malformed_bindings_are_actionable():
    source = Path("episode.ssmd")
    with pytest.raises(SSMDInputError, match="voice_bindings.kokoro must be a mapping") as error:
        document_voice_bindings(
            "---\nvoice_bindings:\n  kokoro: bad\n---\nHello",
            source_path=source,
        )
    assert error.value.source_path == source


def test_captured_multi_role_summary_shape_passes():
    blocks = [
        _voice_block("host", "Opening."),
        _voice_block("analyst", "Context."),
        _voice_block("guest", "Question."),
        _voice_block("analyst", "Analysis."),
        _voice_block("host", "Closing."),
    ]
    result = preflight_ssmd("\n\n".join(blocks), config())
    assert result.ok


def test_preflight_does_not_mutate_source(tmp_path: Path):
    source = tmp_path / "episode.ssmd"
    original = _voice_block("host", "Hello.") + "\n"
    source.write_text(original, encoding="utf-8")
    preflight_ssmd(source.read_text(encoding="utf-8"), config(), source_path=source)
    assert source.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ('<div voice="host">Hello.</div>', "syntax.legacy_div_directive"),
        (
            "---\nssmd_version: '0.8'\n---\n[Hello.]{voice=\"host\"}",
            "header.version_unsupported",
        ),
    ],
)
def test_strict_parser_rejects_legacy_contracts(text: str, code: str) -> None:
    source = Path("episode.ssmd")
    with pytest.raises(SSMDInputError) as error:
        preflight_ssmd(text, config(), source_path=source)

    assert error.value.source_path == source
    assert error.value.details["diagnostics"][0]["code"] == code
