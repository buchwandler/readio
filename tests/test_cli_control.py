from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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
    monkeypatch.setattr(cli.api, "list_voices", lambda **kwargs: (descriptor,))
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
    assert captured["kwargs"]["voicefolio_ref"] == "alice-ref"
    assert captured["kwargs"]["options"] == {"x": "y"}
    assert captured["kwargs"]["make_default"] is True


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
