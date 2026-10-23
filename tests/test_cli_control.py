from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from readio import cli


def test_control_command_parser_families_and_required_source() -> None:
    parser = cli.build_parser()
    parsed = parser.parse_args(["models", "install", "v1", "--engine", "kokoro", "--json"])
    assert parsed.command == "models"
    assert parsed.model_command == "install"
    assert parsed.target == "v1"
    assert parsed.engine == "kokoro"
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["voice", "clone", "--engine", "e", "--target", "t", "--language", "en", "-o", "x"]
        )
    parsed = parser.parse_args(
        [
            "profile",
            "role",
            "set",
            "p.toml",
            "alice",
            "--engine",
            "e",
            "--target",
            "t",
            "--language",
            "en",
            "--voice",
            "v",
            "--in-place",
        ]
    )
    assert parsed.profile_command == "role"
    assert parsed.role_command == "set"
    assert parsed.voice == "v"
    assert parsed.in_place is True


def test_profile_document_inspect_emits_human_and_json_results(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = Path("cast.ssmd.md")
    requirements = SimpleNamespace(
        source=source,
        roles=(SimpleNamespace(name="narrator", blocks=2), SimpleNamespace(name="host", blocks=1)),
        default_role_candidate="narrator",
        uses_default_role=False,
        implicit_blocks=0,
    )
    monkeypatch.setattr(
        cli.api, "inspect_document_voice_requirements", lambda _source: requirements
    )

    assert cli.main(["profile", "document", "inspect", str(source), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["roles"] == [
        {"role": "narrator", "blocks": 2},
        {"role": "host", "blocks": 1},
    ]
    assert payload["result"]["default_role_candidate"] == "narrator"

    assert cli.main(["profile", "document", "inspect", str(source)]) == 0
    output = capsys.readouterr().out
    assert "VOICE ROLES" in output
    assert "narrator" in output and "host" in output
    assert "readio profile document create" in output


def test_profile_document_create_translates_assignments_and_emits_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    source = tmp_path / "cast.ssmd.md"
    output = tmp_path / "cast.renderprofile.toml"
    captured: dict[str, object] = {}
    requirements = SimpleNamespace(
        source=source,
        roles=(SimpleNamespace(name="narrator", blocks=2), SimpleNamespace(name="host", blocks=1)),
    )
    profile = SimpleNamespace(
        roles={"narrator": object(), "host": object()},
        default_role="narrator",
        profile_id="profile-1",
    )
    monkeypatch.setattr(
        cli.api, "inspect_document_voice_requirements", lambda _source: requirements
    )
    monkeypatch.setattr(
        cli.api,
        "create_document_profile",
        lambda source, **kwargs: captured.update(source=source, **kwargs) or profile,
    )

    assert (
        cli.main(
            [
                "profile",
                "document",
                "create",
                str(source),
                "--engine",
                "kokoro",
                "--target",
                "v1.0",
                "--language",
                "en-US",
                "--same-as",
                "analyst=narrator",
                "--voice",
                "narrator=af_heart",
                "--speaker",
                "host=host-speaker",
                "--option",
                "narrator:speed=0.95",
                "--default-role",
                "narrator",
                "--offline",
                "--refresh",
                "-o",
                str(output),
                "--json",
                "--force",
            ]
        )
        == 0
    )
    assert captured == {
        "source": source,
        "engine": "kokoro",
        "target": "v1.0",
        "language": "en-US",
        "voices": {"narrator": "af_heart"},
        "speakers": {"host": "host-speaker"},
        "aliases": {"analyst": "narrator"},
        "options": {"narrator": {"speed": "0.95"}},
        "default_role": "narrator",
        "offline": True,
        "refresh": True,
        "output": output,
        "force": True,
    }
    payload = cast(dict[str, Any], json.loads(capsys.readouterr().out))
    result = cast(dict[str, Any], payload["result"])
    assert result["source"] == str(source)
    assert result["output"] == str(output)
    assert result["bound_roles"] == ["narrator", "host"]
    assert result["aliases"] == {"analyst": "narrator"}
    assert result["profile_id"] == "profile-1"


def test_profile_document_create_human_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    source = tmp_path / "cast.ssmd.md"
    output_path = tmp_path / "cast.renderprofile.toml"
    requirements = SimpleNamespace(
        source=source,
        roles=(SimpleNamespace(name="narrator", blocks=2), SimpleNamespace(name="host", blocks=1)),
    )
    profile = SimpleNamespace(
        roles={"narrator": object(), "host": object()},
        default_role="narrator",
        profile_id="profile-1",
    )
    monkeypatch.setattr(
        cli.api, "inspect_document_voice_requirements", lambda _source: requirements
    )
    monkeypatch.setattr(cli.api, "create_document_profile", lambda _source, **_kwargs: profile)

    assert (
        cli.main(
            [
                "profile",
                "document",
                "create",
                str(source),
                "--engine",
                "kokoro",
                "--target",
                "v1.0",
                "--language",
                "en-US",
                "--voice",
                "narrator=af_heart",
                "--same-as",
                "host=narrator",
                "-o",
                str(output_path),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert f"Created RenderProfile: {output_path}" in output
    assert "Bound roles:\n  narrator\n  host" in output
    assert "Role aliases:\n  host = narrator" in output
    assert "Profile ID: profile-1" in output


def test_profile_document_create_rejects_duplicate_role_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    source = tmp_path / "cast.ssmd.md"
    requirements = SimpleNamespace(source=source, roles=())
    monkeypatch.setattr(
        cli.api, "inspect_document_voice_requirements", lambda _source: requirements
    )
    created = False

    def create_profile(_source: Path, **_kwargs: object) -> object:
        nonlocal created
        created = True
        return object()

    monkeypatch.setattr(cli.api, "create_document_profile", create_profile)
    assert (
        cli.main(
            [
                "profile",
                "document",
                "create",
                str(source),
                "--engine",
                "kokoro",
                "--target",
                "v1.0",
                "--language",
                "en-US",
                "--voice",
                "narrator=af_heart",
                "--option",
                "narrator:speed=1",
                "--option",
                "narrator:speed=2",
                "-o",
                str(tmp_path / "cast.toml"),
                "--json",
            ]
        )
        == 2
    )
    assert not created
    error = json.loads(capsys.readouterr().out)
    assert "supplied more than once" in error["error"]["message"]


def test_profile_document_create_rejects_duplicate_voice_and_alias_assignments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    source = tmp_path / "cast.ssmd.md"
    requirements = SimpleNamespace(source=source, roles=())
    monkeypatch.setattr(
        cli.api, "inspect_document_voice_requirements", lambda _source: requirements
    )
    created = False

    def create_profile(_source: Path, **_kwargs: object) -> object:
        nonlocal created
        created = True
        return object()

    monkeypatch.setattr(cli.api, "create_document_profile", create_profile)
    common = [
        "profile",
        "document",
        "create",
        str(source),
        "--engine",
        "kokoro",
        "--target",
        "v1.0",
        "--language",
        "en-US",
        "-o",
        str(tmp_path / "cast.toml"),
        "--json",
    ]
    for repeated in (
        ["--voice", "narrator=af_heart", "--voice", "narrator=af_sarah"],
        ["--same-as", "analyst=guest", "--same-as", "analyst=host"],
    ):
        assert cli.main([*common, *repeated]) == 2
        error = json.loads(capsys.readouterr().out)["error"]["message"]
        assert "supplied more than once" in error
    assert not created


def test_engines_and_models_cli_forward_public_api_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli.api,
        "list_engine_models",
        lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs) or (),
    )
    assert (
        cli.main(
            ["models", "list", "--engine", "kokoro", "--available", "--lang", "en-US", "--json"]
        )
        == 0
    )
    assert captured == {
        "args": ("kokoro",),
        "kwargs": {
            "language": "en-US",
            "installed": None,
            "available": True,
            "offline": False,
            "refresh": False,
        },
    }
    assert json.loads(capsys.readouterr().out) == {"ok": True, "result": []}


def test_voice_list_human_output_has_contract_columns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    descriptor = SimpleNamespace(
        selector="kokoro:v1/af_heart",
        engine="kokoro",
        target_id="v1",
        voice="af_heart",
        speaker=None,
        languages=("en-US",),
        kind="builtin",
        installed=True,
    )
    monkeypatch.setattr(cli.api, "list_voices", lambda **_kwargs: (descriptor,))
    assert cli.main(["voices", "list", "--engine", "kokoro", "--target", "v1"]) == 0
    output = capsys.readouterr().out.splitlines()
    assert output[0] == "SELECTOR ENGINE TARGET VOICE/SPEAKER LANGUAGE KIND STATUS"
    assert output[1] == "kokoro:v1/af_heart kokoro v1 af_heart en-US builtin installed"


def test_voice_clone_and_profile_role_set_forward_binding_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli.api, "clone_voice", lambda **kwargs: captured.update(kwargs) or "profile"
    )
    output = tmp_path / "alice.toml"
    assert (
        cli.main(
            [
                "voice",
                "clone",
                "--engine",
                "kokoro",
                "--target",
                "clone",
                "--language",
                "en-US",
                "--reference-wav",
                "alice.wav",
                "--transcript",
                "spoken text",
                "--option",
                "temperature=0.5",
                "-o",
                str(output),
                "--json",
            ]
        )
        == 0
    )
    assert captured["target"] == "clone"
    assert captured["reference_wav"] == Path("alice.wav")
    assert captured["options"] == {"temperature": "0.5"}
    assert captured["output"] == output
    assert json.loads(capsys.readouterr().out)["result"] == "profile"

    monkeypatch.setattr(
        cli.api,
        "set_profile_role",
        lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs) or "updated",
    )
    assert (
        cli.main(
            [
                "profile",
                "role",
                "set",
                "narrator.toml",
                "alice",
                "--engine",
                "kokoro",
                "--target",
                "clone",
                "--language",
                "en-US",
                "--voicefolio-ref",
                "alice-ref",
                "--option",
                "x=y",
                "--make-default",
                "--in-place",
            ]
        )
        == 0
    )
    assert captured["args"] == (Path("narrator.toml"), "alice")
    role_kwargs = cast(dict[str, object], captured["kwargs"])
    assert role_kwargs["voicefolio_ref"] == "alice-ref"
    assert role_kwargs["options"] == {"x": "y"}
    assert role_kwargs["make_default"] is True


def test_speak_cli_profile_role_and_structured_usage_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    api_speak = cli.api.speak
    monkeypatch.setattr(
        cli.api,
        "speak",
        lambda text, **kwargs: (
            captured.update(text=text, **kwargs) or SimpleNamespace(audio=[0, 1], sample_rate=24000)
        ),
    )
    output = tmp_path / "speech.wav"
    assert (
        cli.main(
            [
                "speak",
                "Hello",
                "--profile",
                "narrator.toml",
                "--role",
                "alice",
                "--output",
                str(output),
                "--no-play",
                "--json",
            ]
        )
        == 0
    )
    assert captured["profile"] == Path("narrator.toml")
    assert captured["role"] == "alice"
    assert captured["output"] == output
    assert captured["no_play"] is True
    assert json.loads(capsys.readouterr().out)["result"]["played"] is False
    monkeypatch.setattr(cli.api, "speak", api_speak)

    assert cli.main(["speak", "Hello", "--profile", "narrator.toml", "--voice", "x", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["ok"] is False
    assert error["error"]["type"] == "CLIUsageError"
    assert cli.main(["speak", "Hello", "--role", "alice", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["message"] == "--role requires --profile"
