import json
from pathlib import Path

import pytest

from readio import cli
from readio.config import ReadioConfig
from readio.ssmd import analyze_ssmd, preflight_ssmd, resolve_voice_references

FIXTURE = Path(__file__).parent / "fixtures" / "architecture-review.ssmd"


def test_fixture_collects_all_roles_and_runtime_bindings_pass():
    cfg = ReadioConfig()
    text = FIXTURE.read_text(encoding="utf-8")
    analysis = analyze_ssmd(text, cfg)
    assert analysis.unresolved_references == ("moderator", "architect", "skeptic")
    assert {item.reference: item.count for item in analysis.unresolved_voice_references} == {
        "architect": 1,
        "moderator": 1,
        "skeptic": 1,
    }
    result = preflight_ssmd(
        text,
        cfg,
        additional_bindings={
            "moderator": "af_sarah",
            "architect": "am_michael",
            "skeptic": "am_adam",
        },
    )
    assert result.ok


def test_json_error_contains_complete_voice_diagnostic(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())
    with pytest.raises(SystemExit) as error:
        cli.main(["ssmd", "check", str(FIXTURE), "--json"])
    assert error.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "ssmd.unresolved_voice_role"
    assert payload["reference"] == "moderator"
    assert [item["name"] for item in payload["references"]] == [
        "moderator",
        "architect",
        "skeptic",
    ]
    assert payload["available_voices"]
    assert payload["source"].endswith("architecture-review.ssmd")
    assert payload["header_template"]["voice_bindings"]["kokoro"]["architect"] is None


def test_human_error_has_yaml_guidance_and_one_prefix(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())
    with pytest.raises(SystemExit):
        cli.main(["ssmd", "check", str(FIXTURE)])
    output = capsys.readouterr().err
    assert "voice_bindings:" in output
    assert "kokoro:" in output
    assert "readio voices list" in output
    assert "readio: readio:" not in output


def test_json_resolve_never_prompts(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())
    monkeypatch.setattr(cli, "input", lambda prompt: pytest.fail("prompted"), raising=False)
    with pytest.raises(SystemExit) as error:
        cli.main(["ssmd", "check", str(FIXTURE), "--json", "--resolve-voices"])
    assert error.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert "requires an interactive terminal" in payload["error"]


def test_central_voice_resolution_preserves_project_layer_precedence() -> None:
    cfg = ReadioConfig()
    text = '[Hello.]{voice="narrator"}'

    project = resolve_voice_references(text, cfg, project_bindings={"narrator": "af_heart"})[0]
    assert (project.voice, project.origin) == ("af_heart", "project")
    assert project.locator == ("project.settings.ssmd.voice_bindings.kokoro.narrator")

    invocation = resolve_voice_references(
        text,
        cfg,
        additional_bindings={"narrator": "af_bella"},
        project_bindings={"narrator": "af_heart"},
    )[0]
    assert (invocation.voice, invocation.origin) == ("af_bella", "cli")

    document_text = (
        "---\nssmd_version: '0.9'\nvoice_bindings:\n  kokoro:\n    narrator: am_michael\n---\n"
        '[Hello.]{voice="narrator"}'
    )
    document = resolve_voice_references(
        document_text,
        cfg,
        additional_bindings={"narrator": "af_bella"},
        project_bindings={"narrator": "af_heart"},
    )[0]
    assert (document.voice, document.origin) == ("am_michael", "document")

    direct = resolve_voice_references('[Direct.]{voice="am_michael"}', cfg)[0]
    assert (direct.voice, direct.origin) == ("am_michael", "direct")
