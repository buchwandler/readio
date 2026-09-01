from __future__ import annotations

import json
from types import SimpleNamespace

from readio import cli
from readio.models import ModelInfo


def _discovery() -> tuple[tuple[ModelInfo, ...], object]:
    model = ModelInfo(
        id="de-thorsten",
        source="github",
        languages=("de",),
        voices=("thorsten",),
        default_voice="thorsten",
        qualities=("fp32",),
        g2p_backend="kokorog2p",
        lexicons=("gold", "crane"),
        frontend="kokorog2p-de-thorsten-v1",
        status="ready",
        experimental=False,
        runtime_available=True,
        redistribution_allowed=True,
    )
    return (model,), SimpleNamespace(registry_source="fixture", cache_fallback=False)


def test_models_parser_supports_discovery_options() -> None:
    args = cli.build_parser().parse_args(
        ["models", "list", "--language", "de", "--status", "ready", "--offline", "--json"]
    )
    assert args.models_command == "list"
    assert args.language == "de"
    assert args.offline is True


def test_models_list_json_uses_runtime_capabilities(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "discover_model_info", lambda **kwargs: _discovery())
    args = cli.build_parser().parse_args(["models", "list", "--language", "de", "--json"])

    assert cli._cmd_models(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["registry"]["source"] == "fixture"
    assert payload["models"][0]["voices"] == ["thorsten"]
    assert payload["models"][0]["lexicons"] == ["gold", "crane"]
    assert payload["models"][0]["lexicons_known"] is True


def test_models_show_human_output_includes_lexicon_and_voice(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli, "get_model_info", lambda *args, **kwargs: _discovery()[0][0:1] + (_discovery()[1],)
    )
    args = cli.build_parser().parse_args(["models", "show", "de-thorsten"])

    assert cli._cmd_models(args) == 0
    output = capsys.readouterr().out
    assert "Default voice:  thorsten" in output
    assert "  - crane" in output
