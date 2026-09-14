from __future__ import annotations

from readio.lexicons import LexiconCatalogEntry, filter_lexicon_catalog, find_lexicon_entries


def entry(
    selector: str,
    *,
    engine: str = "pykokoro",
    locale: str = "en-US",
    models: tuple[str, ...] = (),
    model_support: str = "unknown",
) -> LexiconCatalogEntry:
    return LexiconCatalogEntry(
        selector=selector,
        engine=engine,
        language=locale.split("-", 1)[0].lower(),
        locale=locale,
        asset_id=f"{locale.lower()}:{selector}",
        data_backend="lexphon",
        default=selector == "gold",
        installed=None,
        models=models,
        model_support=model_support,
    )


def test_exact_and_base_language_filtering_preserves_regional_duplicates() -> None:
    entries = (entry("gold", locale="en-US"), entry("gold", locale="en-GB"))
    assert {item.locale for item in filter_lexicon_catalog(entries, language="en")} == {
        "en-US",
        "en-GB",
    }
    assert [item.locale for item in filter_lexicon_catalog(entries, language="en-us")] == ["en-US"]


def test_selector_is_not_replaced_by_asset_id() -> None:
    item = entry("gold")
    assert item.selector == "gold"
    assert item.to_dict()["selector"] == "gold"
    assert item.to_dict()["asset_id"] == "en-us:gold"


def test_duplicate_selector_across_backends_and_unknown_model_support() -> None:
    entries = (entry("gold"), entry("gold", engine="pipersynth"))
    assert len(filter_lexicon_catalog(entries, selector="gold")) == 2
    assert len(filter_lexicon_catalog(entries, model="future-model")) == 2


def test_known_model_filter_and_asset_lookup() -> None:
    entries = (
        entry("gold", models=("de-thorsten",), model_support="known", locale="de-DE"),
        entry("crane", models=("de-thorsten",), model_support="known", locale="de-DE"),
        entry("other", models=("other",), model_support="known", locale="de-DE"),
    )
    assert [item.selector for item in filter_lexicon_catalog(entries, model="de-thorsten")] == [
        "gold",
        "crane",
    ]
    assert find_lexicon_entries("de-de:crane", entries)[0].selector == "crane"
