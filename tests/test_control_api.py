from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import engines, profiles, voices


def test_model_management_translates_arguments_to_public_voicerender_types(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    result = object()
    monkeypatch.setattr(
        engines.voicerender,
        "list_models",
        lambda engine, **kwargs: captured.update(engine=engine, **kwargs) or (result,),
    )
    assert engines.list_engine_models("kokoro", language="en-US", available=True) == (result,)
    assert captured == {
        "engine": "kokoro",
        "language": "en-US",
        "installed": False,
        "offline": False,
        "refresh": False,
    }

    def install(request: object) -> object:
        captured["request"] = request
        return result

    monkeypatch.setattr(engines.voicerender, "install_model", install)
    assert (
        engines.install_engine_model(
            "v1", engine="kokoro", quality="int8", cache_dir=tmp_path, force=True
        )
        is result
    )
    request = captured["request"]
    assert request.engine == "kokoro"
    assert request.target_id == "v1"
    assert request.quality == "int8"
    assert request.cache_dir == tmp_path
    assert request.force is True


def test_model_list_rejects_conflicting_filters() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        engines.list_engine_models("kokoro", installed=True, available=True)


def test_voice_source_creation_persists_specialist_object(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = {"format": "voicerender.voice-source.v1", "kind": "local-reference", "value": "a.wav"}
    captured: dict[str, object] = {}
    monkeypatch.setattr(voices.voicerender, "create_voice_source", lambda **kwargs: source)
    monkeypatch.setattr(
        voices.voicerender,
        "save_voice_source",
        lambda value, path: (
            captured.update(value=value, path=Path(path)),
            Path(path).write_text("source"),
        ),
    )
    output = tmp_path / "source.toml"
    assert voices.create_voice_source(reference_wav="a.wav", output=output) is source
    assert captured == {"value": source, "path": output}


def test_voice_source_force_does_not_replace_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    monkeypatch.setattr(voices.voicerender, "create_voice_source", lambda **kwargs: {})
    with pytest.raises(ValueError, match="non-file"):
        voices.create_voice_source(reference_wav="a.wav", output=output, force=True)


def test_clone_voice_forwards_request_and_saves_native_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    profile = object()
    monkeypatch.setattr(
        voices.voicerender,
        "clone_voice_profile",
        lambda request, *, role: captured.update(request=request, role=role) or profile,
    )
    monkeypatch.setattr(
        voices.voicerender,
        "save_render_profile",
        lambda value, path: (
            captured.update(saved=value, path=Path(path)),
            Path(path).write_text("profile"),
        ),
    )
    output = tmp_path / "clone.renderprofile.toml"
    assert (
        voices.clone_voice(
            engine="kokoro",
            target="cloning",
            language="en-US",
            role="alice",
            reference_wav="alice.wav",
            transcript="Hello",
            output=output,
        )
        is profile
    )
    request = captured["request"]
    assert request.engine == "kokoro"
    assert request.target_id == "cloning"
    assert request.reference_wav == Path("alice.wav")
    assert request.transcript == "Hello"
    assert captured["role"] == "alice"
    assert captured["saved"] is profile
    assert captured["path"] == output


def test_clone_voice_requires_exactly_one_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        voices.clone_voice(
            engine="kokoro", target="clone", language="en-US", output=tmp_path / "x.toml"
        )


def test_profile_role_uses_specialist_binding_contract_and_explicit_write_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile_path = tmp_path / "profile.toml"
    output = tmp_path / "updated.toml"
    native_profile = object()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        profiles.voicerender, "local_reference_source", lambda path: {"source": str(path)}
    )
    monkeypatch.setattr(
        profiles.voicerender,
        "set_profile_role",
        lambda profile, role, **kwargs: (
            captured.update(profile=profile, role=role, **kwargs) or native_profile
        ),
    )
    monkeypatch.setattr(
        profiles.voicerender,
        "save_render_profile",
        lambda value, path: (
            captured.update(saved=value, path=Path(path)),
            Path(path).write_text("profile"),
        ),
    )
    assert (
        profiles.set_profile_role(
            profile_path,
            "alice",
            engine="kokoro",
            target="clone",
            language="en-US",
            reference_wav="alice.wav",
            make_default=True,
            output=output,
        )
        is native_profile
    )
    assert captured["profile"] == profile_path
    assert captured["role"] == "alice"
    assert captured["target_id"] == "clone"
    assert captured["voice_source"] == profiles.voicerender.local_reference_source("alice.wav")
    assert captured["make_default"] is True
    assert captured["path"] == output

    with pytest.raises(ValueError, match="exactly one"):
        profiles.set_default_profile_role(profile_path, "alice")


def test_profile_refresh_never_overwrites_source_without_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    native = object()
    saved: list[Path] = []
    monkeypatch.setattr(profiles.voicerender, "refresh_render_profile", lambda path: native)
    monkeypatch.setattr(
        profiles.voicerender,
        "save_render_profile",
        lambda value, path: (saved.append(Path(path)), Path(path).write_text("profile")),
    )
    source = tmp_path / "profile.toml"
    assert profiles.refresh_profile(source) is native
    assert saved == []
    destination = tmp_path / "refreshed.toml"
    assert profiles.refresh_profile(source, output=destination) is native
    assert saved == [destination]


def test_voice_list_filters_kind_without_rewriting_specialist_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = SimpleNamespace(kind="VoiceKind.BUILTIN", selector="e:t/v")
    cloned = SimpleNamespace(kind="VoiceKind.CLONED", selector="e:t/c")
    monkeypatch.setattr(voices.voicerender, "list_voices", lambda **kwargs: (built, cloned))
    assert voices.list_voices(engine="e", target="t", kind="cloned") == (cloned,)
