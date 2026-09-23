from __future__ import annotations

import json
from types import SimpleNamespace

from readio import cli
from readio.api.catalog import CatalogService
from readio.lexicons import LexiconCatalogEntry


def _entries() -> tuple[LexiconCatalogEntry, ...]:
    return (
        LexiconCatalogEntry(
            selector="gold",
            engine="pykokoro",
            language="de",
            locale="de-DE",
            asset_id="de-de:gold",
            data_backend="lexphon",
            default=True,
            installed=True,
            models=("de-thorsten",),
            model_support="known",
        ),
        LexiconCatalogEntry(
            selector="crane",
            engine="pykokoro",
            language="de",
            locale="de-DE",
            asset_id="de-de:crane",
            data_backend="lexphon",
            default=False,
            installed=False,
            models=("de-thorsten",),
            model_support="known",
        ),
    )

def _listing() -> SimpleNamespace:
    registry = {
        "source": "fixture",
        "registry_source": "fixture",
        "cache_fallback": False,
        "offline": False,
        "refreshed": False,
    }
    return SimpleNamespace(
        items=_entries(),
        discovery=SimpleNamespace(to_dict=lambda: registry, cache_fallback=False),
    )



def test_lexicons_list_json_uses_named_selectors(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        CatalogService,
        "lexicons_listing",
        lambda self, *args, **kwargs: _listing(),
    )
    args = cli.build_parser().parse_args(["lexicons", "list", "--lang", "de", "--json"])

    assert cli._cmd_lexicons(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"]["language"] == "de"
    assert [item["selector"] for item in payload["lexicons"]] == ["gold", "crane"]
    assert payload["lexicons"][0]["asset_id"] == "de-de:gold"


def test_lexicons_show_human_output_mentions_cli_selector(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        CatalogService,
        "lexicons_listing",
        lambda self, *args, **kwargs: _listing(),
    )
    args = cli.build_parser().parse_args(["lexicons", "show", "gold", "--language", "de"])

    assert cli._cmd_lexicons(args) == 0
    output = capsys.readouterr().out
    assert "Selector:       gold" in output
    assert "Use with:       --lexicon gold" in output
    assert "de-de:gold" in output


def test_lexicons_list_parser_supports_filters() -> None:
    args = cli.build_parser().parse_args(
        [
            "lexicons",
            "list",
            "--language",
            "en",
            "--model",
            "model",
            "--engine",
            "pykokoro",
            "--offline",
            "--refresh",
            "--preference",
            "github",
        ]
    )
    assert args.language == "en"
    assert args.model == "model"
    assert args.engine == "pykokoro"
    assert args.offline is True
    assert args.refresh is True


def test_lexicons_list_without_language_handles_missing_assets(capsys) -> None:
    args = cli.build_parser().parse_args(["lexicons", "list", "--offline", "--json"])

    assert cli._cmd_lexicons(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lexicons"]
