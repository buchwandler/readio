from __future__ import annotations

from pathlib import Path

import pytest

from readio import profile_source
from readio.profile_source import (
    create_document_profile,
    inspect_document_voice_requirements,
)


def _write_ssmd(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_inspection_counts_roles_and_prefers_narrator_without_losing_source_order(
    tmp_path: Path,
) -> None:
    source = _write_ssmd(
        tmp_path / "cast.ssmd.md",
        """:::{voice="host"}
First host block.
:::
:::{voice="narrator"}
Narration.
:::
:::{voice="guest"}
Guest.
:::
:::{voice="host"}
Second host block.
:::""",
    )

    requirements = inspect_document_voice_requirements(source)

    assert requirements.source == source.resolve()
    assert [(role.name, role.blocks) for role in requirements.roles] == [
        ("narrator", 1),
        ("host", 2),
        ("guest", 1),
    ]
    assert requirements.default_role_candidate == "narrator"
    assert requirements.uses_default_role is False
    assert requirements.implicit_blocks == 0


def test_inspection_uses_first_appearance_when_narrator_is_absent(tmp_path: Path) -> None:
    source = _write_ssmd(
        tmp_path / "cast.ssmd.md",
        ':::{voice="host"}\nHello.\n:::\n\n:::{voice="analyst"}\nHi.\n:::\n',
    )

    requirements = inspect_document_voice_requirements(source)

    assert [(role.name, role.blocks) for role in requirements.roles] == [
        ("host", 1),
        ("analyst", 1),
    ]
    assert requirements.default_role_candidate == "host"


def test_inspection_reports_implicit_default_without_inventing_narrator(tmp_path: Path) -> None:
    source = _write_ssmd(tmp_path / "plain.ssmd.md", "Plain unvoiced text.\n")

    requirements = inspect_document_voice_requirements(source)

    assert requirements.roles == ()
    assert requirements.default_role_candidate is None
    assert requirements.uses_default_role is True
    assert requirements.implicit_blocks == 1


def test_inspection_requires_existing_ssmd_markdown_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="SSMD source does not exist"):
        inspect_document_voice_requirements(tmp_path / "missing.ssmd.md")

    ordinary = _write_ssmd(tmp_path / "document.md", "not SSMD")
    with pytest.raises(ValueError, match="supports .ssmd.md"):
        inspect_document_voice_requirements(ordinary)

    with pytest.raises(ValueError, match="is not a file"):
        inspect_document_voice_requirements(tmp_path)


def _profile_source(path: Path) -> Path:
    return _write_ssmd(
        path,
        """:::{voice=\"narrator\"}
Narration.
:::
:::{voice=\"host\"}
Host.
:::
:::{voice=\"guest\"}
Guest.
:::
:::{voice=\"analyst\"}
Analysis.
:::""",
    )


def test_create_document_profile_builds_and_saves_one_complete_native_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _profile_source(tmp_path / "cast.ssmd.md")
    output = tmp_path / "cast.renderprofile.toml"
    native_profile = object()
    captured: dict[str, object] = {}
    role_calls: list[tuple[str, dict[str, object]]] = []
    saved: list[Path] = []

    def create(**kwargs: object) -> object:
        captured.update(create=kwargs)
        return native_profile

    def set_role(profile: object, role: str, **kwargs: object) -> object:
        assert profile is native_profile
        role_calls.append((role, kwargs))
        return native_profile

    monkeypatch.setattr(profile_source.voicerender, "create_render_profile", create)
    monkeypatch.setattr(profile_source.voicerender, "set_profile_role", set_role)
    monkeypatch.setattr(
        profile_source.voicerender,
        "validate_render_profile",
        lambda profile: captured.update(validated=profile) or profile,
    )
    monkeypatch.setattr(
        profile_source.voicerender,
        "save_render_profile",
        lambda _profile, path: (
            saved.append(Path(path)),
            Path(path).write_text("native profile", encoding="utf-8"),
        ),
    )

    result = create_document_profile(
        source,
        engine="fallback-engine",
        target="fallback-target",
        language="en-US",
        voices={
            "narrator": "kokoro:v1.0/af_heart",
            "guest": "am_michael",
        },
        speakers={"host": "host-speaker"},
        aliases={"analyst": "guest"},
        options={"guest": {"speed": 0.95}},
        offline=True,
        refresh=True,
        output=output,
    )

    assert result is native_profile
    assert captured["validated"] is native_profile
    assert captured["create"] == {
        "engine": "kokoro",
        "target_id": "v1.0",
        "language": "en-US",
        "role": "narrator",
        "voice": "af_heart",
        "speaker": None,
        "options": {},
        "offline": True,
        "refresh": True,
    }
    assert [role for role, _kwargs in role_calls] == ["host", "guest", "analyst"]
    assert role_calls[0][1]["speaker"] == "host-speaker"
    assert role_calls[1][1]["engine"] == "fallback-engine"
    assert role_calls[1][1]["target_id"] == "fallback-target"
    assert role_calls[1][1]["options"] == {"speed": 0.95}
    assert role_calls[2][1]["options"] == role_calls[1][1]["options"]
    assert role_calls[2][1]["options"] is not role_calls[1][1]["options"]
    assert all(call[1]["offline"] is True and call[1]["refresh"] is True for call in role_calls)
    assert saved == [output]
    assert output.read_text(encoding="utf-8") == "native profile"


def test_create_document_profile_rejects_incomplete_bindings_before_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _profile_source(tmp_path / "cast.ssmd.md")
    output = tmp_path / "cast.renderprofile.toml"
    monkeypatch.setattr(
        profile_source.voicerender,
        "create_render_profile",
        lambda **_kwargs: pytest.fail("profile creation must not start before coverage passes"),
    )

    with pytest.raises(ValueError, match="missing role bindings: analyst"):
        create_document_profile(
            source,
            engine="kokoro",
            target="v1.0",
            language="en-US",
            voices={"narrator": "af_heart", "host": "af_sarah", "guest": "am_michael"},
            output=output,
        )

    assert not output.exists()


@pytest.mark.parametrize(
    ("voices", "speakers", "aliases", "message"),
    [
        (
            {"narrator": "af_heart", "host": "af_sarah", "guest": "am_michael", "other": "x"},
            {},
            {},
            "unknown source role",
        ),
        (
            {"narrator": "af_heart", "host": "af_sarah", "guest": "am_michael"},
            {},
            {"analyst": "analyst"},
            "alias of itself",
        ),
        (
            {"narrator": "af_heart", "host": "af_sarah"},
            {},
            {"analyst": "guest", "guest": "analyst"},
            "cyclic role alias",
        ),
        (
            {
                "narrator": "af_heart",
                "host": "af_sarah",
                "guest": "am_michael",
                "analyst": "af_sarah",
            },
            {},
            {"analyst": "guest"},
            "both a direct binding and a --same-as alias",
        ),
    ],
)
def test_create_document_profile_rejects_invalid_role_mappings_before_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    voices: dict[str, str],
    speakers: dict[str, str | int],
    aliases: dict[str, str],
    message: str,
) -> None:
    source = _profile_source(tmp_path / "cast.ssmd.md")
    output = tmp_path / "cast.renderprofile.toml"
    monkeypatch.setattr(
        profile_source.voicerender,
        "create_render_profile",
        lambda **_kwargs: pytest.fail("invalid mappings must fail before native profile creation"),
    )

    with pytest.raises(ValueError, match=message):
        create_document_profile(
            source,
            engine="kokoro",
            target="v1.0",
            language="en-US",
            voices=voices,
            speakers=speakers,
            aliases=aliases,
            output=output,
        )

    assert not output.exists()


def test_create_document_profile_requires_a_binding_for_unvoiced_documents(
    tmp_path: Path,
) -> None:
    source = _write_ssmd(tmp_path / "plain.ssmd.md", "No voice directives.\n")

    with pytest.raises(ValueError, match="no explicit voice roles.*use 'readio profile create'"):
        create_document_profile(
            source,
            engine="kokoro",
            target="v1.0",
            language="en-US",
            voices={"narrator": "af_heart"},
            output=tmp_path / "plain.renderprofile.toml",
        )


def test_create_document_profile_rejects_voice_and_speaker_for_same_role(
    tmp_path: Path,
) -> None:
    source = _profile_source(tmp_path / "cast.ssmd.md")

    with pytest.raises(ValueError, match="cannot use both --voice and --speaker"):
        create_document_profile(
            source,
            engine="kokoro",
            target="v1.0",
            language="en-US",
            voices={"narrator": "af_heart"},
            speakers={"narrator": "speaker"},
            output=tmp_path / "cast.renderprofile.toml",
        )


def test_create_document_profile_honors_explicit_default_role(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _profile_source(tmp_path / "cast.ssmd.md")
    native_profile = object()
    captured: dict[str, object] = {}
    added_roles: list[str] = []
    output = tmp_path / "cast.renderprofile.toml"
    monkeypatch.setattr(
        profile_source.voicerender,
        "create_render_profile",
        lambda **kwargs: captured.update(kwargs) or native_profile,
    )
    monkeypatch.setattr(
        profile_source.voicerender,
        "set_profile_role",
        lambda profile, role, **_kwargs: (added_roles.append(role), profile)[1],
    )
    monkeypatch.setattr(
        profile_source.voicerender, "validate_render_profile", lambda profile: profile
    )
    monkeypatch.setattr(
        profile_source.voicerender,
        "save_render_profile",
        lambda _profile, path: Path(path).write_text("profile", encoding="utf-8"),
    )

    create_document_profile(
        source,
        engine="kokoro",
        target="v1.0",
        language="en-US",
        voices={"narrator": "af_heart", "host": "af_sarah", "guest": "am_michael"},
        aliases={"analyst": "guest"},
        default_role="guest",
        output=output,
    )

    assert captured["role"] == "guest"
    assert captured["voice"] == "am_michael"
    assert added_roles == ["narrator", "host", "analyst"]
    assert output.is_file()


def test_create_document_profile_rejects_unknown_default_before_native_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _profile_source(tmp_path / "cast.ssmd.md")
    output = tmp_path / "cast.renderprofile.toml"
    monkeypatch.setattr(
        profile_source.voicerender,
        "create_render_profile",
        lambda **_kwargs: pytest.fail("invalid default must fail before native profile creation"),
    )

    with pytest.raises(ValueError, match="default role 'other' is not present"):
        create_document_profile(
            source,
            engine="kokoro",
            target="v1.0",
            language="en-US",
            voices={
                "narrator": "af_heart",
                "host": "af_sarah",
                "guest": "am_michael",
                "analyst": "af_sarah",
            },
            default_role="other",
            output=output,
        )

    assert not output.exists()


def test_create_document_profile_preserves_existing_output_unless_forced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _profile_source(tmp_path / "cast.ssmd.md")
    output = tmp_path / "cast.renderprofile.toml"
    output.write_text("original", encoding="utf-8")
    native_profile = object()
    saved: list[Path] = []
    monkeypatch.setattr(
        profile_source.voicerender, "create_render_profile", lambda **_kwargs: native_profile
    )
    monkeypatch.setattr(
        profile_source.voicerender,
        "set_profile_role",
        lambda profile, _role, **_kwargs: profile,
    )
    monkeypatch.setattr(
        profile_source.voicerender, "validate_render_profile", lambda profile: profile
    )
    monkeypatch.setattr(
        profile_source.voicerender,
        "save_render_profile",
        lambda _profile, path: (
            saved.append(Path(path)),
            Path(path).write_text("native profile", encoding="utf-8"),
        ),
    )

    def create_profile(*, force: bool = False) -> object:
        return create_document_profile(
            source,
            engine="kokoro",
            target="v1.0",
            language="en-US",
            voices={
                "narrator": "af_heart",
                "host": "af_sarah",
                "guest": "am_michael",
                "analyst": "am_michael",
            },
            output=output,
            force=force,
        )

    with pytest.raises(FileExistsError, match="output already exists"):
        create_profile()
    create_profile(force=True)
    assert output.read_text(encoding="utf-8") == "native profile"
    assert len(saved) == 1
    assert saved[0].name == output.name
