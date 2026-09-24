from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from readio import cli
from readio.api import Readio, default_config
from readio.api.catalog import CatalogService
from readio.config import PathSettings, ReadioConfig, VoiceProviderSettings
from readio.models import ModelInfo, VoiceMetadata
from readio.voices import VoiceCatalogEntry, build_voice_catalog


def real_en_us_catalog() -> tuple[VoiceCatalogEntry, ...]:
    voices = (
        "af_alloy",
        "af_aoede",
        "af_bella",
        "af_heart",
        "af_jessica",
        "af_kore",
        "af_nicole",
        "af_nova",
        "af_river",
        "af_sarah",
        "af_sky",
        "am_adam",
        "am_echo",
        "am_eric",
        "am_fenrir",
        "am_liam",
        "am_michael",
        "am_onyx",
        "am_puck",
        "am_santa",
        "af_ameliaearhart",
        "af_libritts5338",
        "am_libritts1272",
        "am_libritts6241",
        "am_vincentprice",
    )

    def make_model(model_id: str, model_voices: tuple[str, ...]) -> ModelInfo:
        return ModelInfo(
            id=model_id,
            source="github",
            languages=("en",),
            voices=model_voices,
            default_voice=model_voices[0],
            qualities=("fp32",),
            g2p_backend="kokorog2p",
            lexicons=(),
            frontend="frontend",
            status="ready",
            experimental=False,
            runtime_available=True,
            redistribution_allowed=True,
            voice_details=tuple(
                VoiceMetadata(voice, "unknown", "en", "en-US", "American English")
                for voice in model_voices
            ),
        )

    models = (
        make_model("v1.0", voices),
        make_model("v1.1-zh", ("af_maple", "af_sol")),
    )
    return build_voice_catalog(models).voices


def config(tmp_path: Path) -> ReadioConfig:
    return ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "out"),
        voices={
            "kokoro": VoiceProviderSettings(
                ids=("af_sarah", "am_michael"),
                roles={"host": "af_sarah"},
            )
        },
    )


def catalog_entry() -> VoiceCatalogEntry:
    return VoiceCatalogEntry(
        selector="de-ko-3",
        slot=3,
        id="martin",
        gender="male",
        language="de",
        locale="de",
        language_label="German",
        model="de-model",
        source="github",
        default=True,
        status="ready",
        experimental=False,
        runtime_available=True,
    )


def _patch_voice_listing(monkeypatch, entries, *, registry_source="fixture", calls=None) -> None:
    def listing(self, query=None, *, discovery=None):
        if calls is not None:
            calls.append(query)
        metadata = {
            "source": registry_source,
            "registry_source": registry_source,
            "cache_fallback": False,
            "offline": bool(discovery and discovery.offline),
            "refreshed": bool(discovery and discovery.refresh),
        }
        return SimpleNamespace(
            items=entries,
            discovery=SimpleNamespace(to_dict=lambda: metadata, cache_fallback=False),
        )

    monkeypatch.setattr(CatalogService, "voices_listing", listing)


def patch_catalog(monkeypatch) -> None:
    _patch_voice_listing(monkeypatch, (catalog_entry(),), registry_source="cache")


def test_voices_list_and_show_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: config(tmp_path))
    patch_catalog(monkeypatch)

    assert (
        cli._cmd_voices(cli.build_parser().parse_args(["voices", "list", "--lang", "de", "--json"]))
        == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert listed["filters"]["language"] == "de"
    assert listed["voices"][0]["selector"] == "de-ko-3"
    assert listed["voices"][0]["id"] == "martin"

    assert (
        cli._cmd_voices(cli.build_parser().parse_args(["voices", "show", "de-ko-3", "--json"])) == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["voice"]["selector"] == "de-ko-3"
    assert shown["voice"]["model"] == "de-model"
    assert shown["registry"]["source"] == "cache"


def test_voices_list_en_us_uses_real_registry_and_show_canonicalizes_hyphens(monkeypatch, capsys):
    entries = real_en_us_catalog()
    _patch_voice_listing(monkeypatch, entries, registry_source="packaged")
    args = cli.build_parser().parse_args(
        ["voices", "list", "--engine", "kokoro", "--lang", "en-us", "--json"]
    )
    assert cli._cmd_voices(args) == 0
    listed = json.loads(capsys.readouterr().out)
    voices = listed["voices"]
    assert len(voices) == 27
    assert [voice["selector"] for voice in voices] == [f"en_us-ko-{i}" for i in range(1, 28)]
    assert all(voice["selector"] is not None for voice in voices)
    assert [voice["model"] for voice in voices[:25]] == ["v1.0"] * 25
    assert [voice["model"] for voice in voices[25:]] == ["v1.1-zh"] * 2
    by_id = {voice["id"]: voice for voice in voices}
    assert by_id["af_heart"]["selector"] == "en_us-ko-4"
    assert by_id["af_maple"]["selector"] == "en_us-ko-26"
    assert by_id["af_sol"]["selector"] == "en_us-ko-27"
    for requested in ("en_us-ko-4", "en-us-ko-4"):
        args = cli.build_parser().parse_args(["voices", "show", requested, "--json"])
        assert cli._cmd_voices(args) == 0
        shown = json.loads(capsys.readouterr().out)["voice"]
        assert (shown["selector"], shown["id"], shown["model"]) == (
            "en_us-ko-4",
            "af_heart",
            "v1.0",
        )


def test_pipersynth_alias_filters_canonical_piper(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: config(tmp_path))
    piper_entry = VoiceCatalogEntry(
        selector="de-pi-9",
        slot=9,
        id="de_DE-thorsten-medium",
        gender="unknown",
        language="de",
        locale="de-DE",
        language_label="DE",
        model="de_DE-thorsten-medium",
        source="pipersynth",
        default=False,
        status="ready",
        experimental=False,
        runtime_available=True,
        engine="piper",
    )
    _patch_voice_listing(monkeypatch, (piper_entry,), registry_source="engine-adapters")
    args = cli.build_parser().parse_args(["voices", "list", "--engine", "pipersynth", "--json"])
    assert cli._cmd_voices(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"]["engine"] == "piper"
    assert payload["voices"][0]["engine"] == "piper"


def test_roles_bind_and_unbind_use_config_save(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    saved = []
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)
    monkeypatch.setattr(
        "readio.config.save_config",
        lambda updated: saved.append(updated) or Path("config.toml"),
    )
    monkeypatch.setattr("readio.api.roles.resolve_voice_selector", lambda *args, **kwargs: None)

    assert (
        cli._cmd_roles(cli.build_parser().parse_args(["roles", "bind", "moderator", "new_voice"]))
        == 0
    )
    assert saved[-1].voices["kokoro"].roles["moderator"] == "new_voice"
    assert "new_voice" in saved[-1].voices["kokoro"].ids

    bound = saved[-1]
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: bound)
    assert cli._cmd_roles(cli.build_parser().parse_args(["roles", "unbind", "moderator"])) == 0
    assert "moderator" not in saved[-1].voices["kokoro"].roles


def test_legacy_roles_alias_emits_warning(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: config(tmp_path))
    assert cli._cmd_voices(cli.build_parser().parse_args(["voices", "roles", "--json"])) == 0
    assert "deprecated" in capsys.readouterr().err


def test_voice_list_model_engine_aliases_normalize_without_changing_concrete_filters():
    normalize_engine = Readio(default_config()).catalog.normalize_engine
    available = {"piper", "pykokoro"}
    assert [
        cli._normalize_voice_list_filters(
            engine=None,
            model=model,
            available_engines=available,
            normalize_engine=normalize_engine,
        )
        for model in ("piper", "pipersynth", "pykokoro", "kokoro")
    ] == [
        ("piper", None),
        ("piper", None),
        ("pykokoro", None),
        ("pykokoro", None),
    ]
    assert cli._normalize_voice_list_filters(
        engine=None,
        model="v1.0",
        available_engines=available,
        normalize_engine=normalize_engine,
    ) == (None, "v1.0")
    assert cli._normalize_voice_list_filters(
        engine="pipersynth",
        model="en_US-amy-medium",
        available_engines=available,
        normalize_engine=normalize_engine,
    ) == ("piper", "en_US-amy-medium")
    assert cli._normalize_voice_list_filters(
        engine="PIPERSYNTH",
        model=None,
        available_engines=available,
        normalize_engine=normalize_engine,
    ) == ("piper", None)


def test_model_piper_alias_uses_engine_discovery_and_preserves_target_filter(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: config(tmp_path))
    piper_entry = VoiceCatalogEntry(
        selector="de-pi-9",
        slot=9,
        id="de_DE-thorsten-medium",
        gender="unknown",
        language="de",
        locale="de-DE",
        language_label="German",
        model="de_DE-thorsten-medium",
        source="pipersynth",
        default=False,
        status="ready",
        experimental=False,
        runtime_available=True,
        engine="piper",
    )
    calls = []
    _patch_voice_listing(
        monkeypatch,
        (piper_entry,),
        registry_source="engine-adapters",
        calls=calls,
    )

    alias_args = cli.build_parser().parse_args(["voices", "list", "--model", "piper", "--json"])
    assert cli._cmd_voices(alias_args) == 0
    alias_payload = json.loads(capsys.readouterr().out)
    assert calls[-1].engine == "piper"
    assert alias_payload["filters"]["engine"] == "piper"
    assert alias_payload["filters"]["model"] is None
    assert alias_payload["voices"][0]["id"] == "de_DE-thorsten-medium"

    target_args = cli.build_parser().parse_args(
        [
            "voices",
            "list",
            "--engine",
            "piper",
            "--model",
            "de_DE-thorsten-medium",
            "--json",
        ]
    )
    assert cli._cmd_voices(target_args) == 0
    target_payload = json.loads(capsys.readouterr().out)
    assert calls[-1].engine == "piper"
    assert target_payload["filters"]["engine"] == "piper"
    assert target_payload["filters"]["model"] == "de_DE-thorsten-medium"
    assert target_payload["voices"][0]["id"] == "de_DE-thorsten-medium"
