from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import cli
from readio.api import Readio, default_config
from readio.api.catalog import CatalogService
from readio.api.types import CatalogDiscovery, CatalogListing, VoiceInfo, VoicePromptInfo
from readio.config import PathSettings, ReadioConfig
from readio.models import ModelInfo, VoiceMetadata
from readio.role_targets import VoiceTarget
from readio.voice_refs import public_system_for_engine
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
        paths=PathSettings(output=tmp_path / "out"),
        roles={"host": VoiceTarget("kokoro", "af_sarah")},
    )


def catalog_entry() -> VoiceCatalogEntry:
    return VoiceCatalogEntry(
        ref="kokoro:de-model/martin",
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
        items = tuple(
            VoiceInfo(
                ref=entry.ref,
                id=entry.id,
                gender=entry.gender,
                language=entry.language,
                locale=entry.locale,
                language_label=entry.language_label,
                model=entry.model,
                source=entry.source,
                default=entry.default,
                status=entry.status,
                experimental=entry.experimental,
                runtime_available=entry.runtime_available,
                distribution_id=entry.distribution_id,
                provider=entry.provider,
                engine=public_system_for_engine(entry.engine),
            )
            for entry in entries
        )
        return SimpleNamespace(
            items=items,
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
    assert listed["voices"][0]["ref"] == "kokoro:de-model/martin"
    assert listed["voices"][0]["id"] == "martin"

    assert (
        cli._cmd_voices(
            cli.build_parser().parse_args(["voices", "show", "kokoro:de-model/martin", "--json"])
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["voice"]["ref"] == "kokoro:de-model/martin"
    assert shown["voice"]["model"] == "de-model"
    assert shown["registry"]["source"] == "cache"


def test_voice_prompts_cli_json_filters_and_reports_metadata(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: config(tmp_path))
    prompt = VoicePromptInfo(
        engine="pocket",
        ref="kyutai-tts-voices:alba/casual",
        source_repository="kyutai/voices",
        source_revision="catalog-rev-3",
        source_path="alba/casual.wav",
        size=1234,
        sha256="a" * 64,
        license="cc-by-4.0",
        dataset="alba",
        variant="casual",
    )
    calls = []

    def listing(self, query, *, discovery):
        calls.append((query, discovery))
        return CatalogListing(
            (prompt,),
            CatalogDiscovery(
                registry_source="pocketsynth-public-metadata",
                offline=discovery.offline,
                refreshed=discovery.refresh,
            ),
        )

    monkeypatch.setattr(CatalogService, "voice_prompts_listing", listing)
    args = cli.build_parser().parse_args(
        [
            "voices",
            "prompts",
            "--engine",
            "pocket",
            "--dataset",
            "alba",
            "--variant",
            "casual",
            "--license",
            "cc-by-4.0",
            "--offline",
            "--refresh",
            "--json",
        ]
    )

    assert cli._cmd_voices(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"] == {
        "engine": "pocket",
        "dataset": "alba",
        "variant": "casual",
        "license": "cc-by-4.0",
    }
    assert payload["prompts"][0]["ref"] == prompt.ref
    assert payload["prompts"][0]["sha256"] == prompt.sha256
    assert calls[0][0].engine == "pocket"
    assert calls[0][0].dataset == "alba"
    assert calls[0][0].variant == "casual"
    assert calls[0][0].license == "cc-by-4.0"
    assert calls[0][1].offline is True
    assert calls[0][1].refresh is True


def test_voices_list_en_us_uses_real_registry_and_shows_semantic_reference(monkeypatch, capsys):
    entries = real_en_us_catalog()
    _patch_voice_listing(monkeypatch, entries, registry_source="packaged")
    args = cli.build_parser().parse_args(
        ["voices", "list", "--engine", "kokoro", "--lang", "en-us", "--json"]
    )
    assert cli._cmd_voices(args) == 0
    listed = json.loads(capsys.readouterr().out)
    voices = listed["voices"]
    assert len(voices) == 27
    assert [voice["ref"] for voice in voices] == [
        f"kokoro:{voice['model']}/{voice['id']}" for voice in voices
    ]
    assert all("selector" not in voice for voice in voices)
    assert [voice["model"] for voice in voices[:25]] == ["v1.0"] * 25
    assert [voice["model"] for voice in voices[25:]] == ["v1.1-zh"] * 2
    by_id = {voice["id"]: voice for voice in voices}
    assert by_id["af_heart"]["ref"] == "kokoro:v1.0/af_heart"
    assert by_id["af_maple"]["ref"] == "kokoro:v1.1-zh/af_maple"
    assert by_id["af_sol"]["ref"] == "kokoro:v1.1-zh/af_sol"

    args = cli.build_parser().parse_args(["voices", "show", "kokoro:v1.0/af_heart", "--json"])
    assert cli._cmd_voices(args) == 0
    shown = json.loads(capsys.readouterr().out)["voice"]
    assert (shown["ref"], shown["id"], shown["model"]) == (
        "kokoro:v1.0/af_heart",
        "af_heart",
        "v1.0",
    )


def test_pipersynth_alias_filters_canonical_piper(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: config(tmp_path))
    piper_entry = VoiceCatalogEntry(
        ref="piper:de_DE-thorsten-medium",
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
    monkeypatch.setattr(
        "readio.api.roles.resolve_voice_reference",
        lambda voice, **kwargs: SimpleNamespace(engine="pykokoro", voice=voice, target_id="v1.0"),
    )

    assert (
        cli._cmd_roles(cli.build_parser().parse_args(["roles", "bind", "moderator", "new_voice"]))
        == 0
    )
    assert saved[-1].roles["moderator"].voice == "new_voice"
    assert saved[-1].roles["moderator"].engine == "kokoro"

    bound = saved[-1]
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: bound)
    assert cli._cmd_roles(cli.build_parser().parse_args(["roles", "unbind", "moderator"])) == 0
    assert "moderator" not in saved[-1].roles


def test_role_cli_json_reports_canonical_engine_per_binding(monkeypatch, tmp_path, capsys):
    cfg = config(tmp_path)
    saved = []
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)
    monkeypatch.setattr(
        "readio.config.save_config",
        lambda updated: saved.append(updated) or Path("config.toml"),
    )
    monkeypatch.setattr(
        "readio.api.roles.resolve_voice_reference",
        lambda voice, **kwargs: SimpleNamespace(
            engine=kwargs.get("engine") or "pykokoro",
            voice=voice,
            target_id="v1.0",
        ),
    )

    bind_args = cli.build_parser().parse_args(
        ["roles", "bind", "guest", "en_US-amy-medium", "--engine", "piper", "--json"]
    )
    assert cli._cmd_roles(bind_args) == 0
    bound = json.loads(capsys.readouterr().out)
    assert bound["engine"] == "piper"
    assert "provider" not in bound
    assert bound["voice"] == "en_US-amy-medium"

    cfg = saved[-1]
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)
    list_args = cli.build_parser().parse_args(["roles", "list", "--json"])
    assert cli._cmd_roles(list_args) == 0
    listing = json.loads(capsys.readouterr().out)
    guest = next(item for item in listing["roles"] if item["role"] == "guest")
    assert guest["engine"] == "piper"
    assert "provider" not in guest

    unbind_args = cli.build_parser().parse_args(["roles", "unbind", "guest", "--json"])
    assert cli._cmd_roles(unbind_args) == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed["removed_target"]["engine"] == "piper"
    assert removed["engine"] == "piper"


@pytest.mark.parametrize("legacy_command", ("roles", "bind", "unbind"))
def test_deprecated_voice_role_aliases_are_removed(legacy_command):
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(["voices", legacy_command])
    assert exit_info.value.code == 2


def test_voice_list_model_engine_aliases_normalize_without_changing_concrete_filters():
    normalize_engine = Readio(default_config()).catalog.normalize_engine
    available = {"piper", "kokoro"}
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
        ("kokoro", None),
        ("kokoro", None),
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
        ref="piper:de_DE-thorsten-medium",
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


def test_voice_cli_json_and_table_share_normalized_piper_metadata(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: config(tmp_path))
    monkeypatch.setattr(
        CatalogService,
        "engines",
        lambda _self: (SimpleNamespace(id="piper"), SimpleNamespace(id="pocket")),
    )
    entries = (
        VoiceCatalogEntry(
            ref="piper:en_US-amy-medium",
            id="en_US-amy-medium",
            gender="female",
            language="en",
            locale="en-us",
            language_label="American English",
            model="en_US-amy-medium",
            source="pipersynth",
            default=False,
            status="ready",
            experimental=False,
            runtime_available=True,
            engine="piper",
        ),
        VoiceCatalogEntry(
            ref="piper:en_US-lee-medium",
            id="en_US-lee-medium",
            gender="unknown",
            language="en",
            locale="en-us",
            language_label="en-us",
            model="en_US-lee-medium",
            source="pipersynth",
            default=False,
            status="ready",
            experimental=False,
            runtime_available=True,
            engine="piper",
        ),
    )
    discovery = SimpleNamespace(
        registry_source="fixture", cache_fallback=False, offline=False, refreshed=False
    )
    monkeypatch.setattr(
        "readio.api.catalog.discover_voice_catalog",
        lambda **_kwargs: (entries, discovery),
    )

    json_args = cli.build_parser().parse_args(
        ["voices", "list", "--engine", "piper", "--lang", "en-us", "--json"]
    )
    assert cli._cmd_voices(json_args) == 0
    voices = json.loads(capsys.readouterr().out)["voices"]
    assert [voice["ref"] for voice in voices] == [
        "piper:en_US-amy-medium",
        "piper:en_US-lee-medium",
    ]
    assert all("selector" not in voice for voice in voices)
    assert [voice["locale"] for voice in voices] == ["en-us", "en-us"]
    assert [voice["language"] for voice in voices] == ["en", "en"]
    assert [voice["gender"] for voice in voices] == ["female", "unknown"]
    assert voices[1]["language_label"] == "en-us"

    table_args = cli.build_parser().parse_args(
        ["voices", "list", "--engine", "piper", "--lang", "en-us"]
    )
    assert cli._cmd_voices(table_args) == 0
    table = capsys.readouterr().out
    assert "piper:en_US-amy-medium" in table and "female" in table
    assert "piper:en_US-lee-medium" in table and "unknown" in table
    assert "en-us" in table and "American English" in table


def test_pocket_voice_cli_includes_generic_bundle_for_specific_language(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: config(tmp_path))
    entry = VoiceCatalogEntry(
        ref="pocket:generic-english/alba",
        id="alba",
        gender="female",
        language="en",
        locale="en",
        language_label="English",
        model="generic-english",
        source="pocket",
        default=True,
        status="ready",
        experimental=False,
        runtime_available=True,
        engine="pocket",
    )
    discovery = SimpleNamespace(
        registry_source="engine-adapters",
        cache_fallback=False,
        offline=False,
        refreshed=False,
    )
    monkeypatch.setattr(
        "readio.api.catalog.discover_voice_catalog",
        lambda **_kwargs: ((entry,), discovery),
    )

    args = cli.build_parser().parse_args(
        ["voices", "list", "--engine", "pocket", "--lang", "en-us", "--json"]
    )
    assert cli._cmd_voices(args) == 0
    voices = json.loads(capsys.readouterr().out)["voices"]
    assert [voice["id"] for voice in voices] == ["alba"]
    assert voices[0]["ref"] == "pocket:generic-english/alba"
    assert voices[0]["locale"] == "en"
    assert voices[0]["gender"] == "female"
    assert "selector" not in voices[0]
