from __future__ import annotations

import pytest

from readio.voice_refs import (
    VoiceRef,
    engine_for_public_system,
    format_voice_ref,
    is_voice_ref,
    parse_voice_ref,
    public_system_for_engine,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("kokoro:v1.0/af_heart", VoiceRef("kokoro", "v1.0", "af_heart")),
        ("piper:en_US-amy-medium", VoiceRef("piper", "en_US-amy-medium")),
        (
            "pocket:english_2026-04/alba",
            VoiceRef("pocket", "english_2026-04", "alba"),
        ),
        ("kitten:nano-0.8-int8/Jasper", VoiceRef("kitten", "nano-0.8-int8", "Jasper")),
        ("inflect:nano-v2/default", VoiceRef("inflect", "nano-v2", "default")),
        ("inflect:micro-v2/default", VoiceRef("inflect", "micro-v2", "default")),
    ],
)
def test_parse_and_format_voice_ref(value: str, expected: VoiceRef) -> None:
    reference = parse_voice_ref(value)
    assert reference == expected
    assert format_voice_ref(reference) == value
    assert reference.value == value
    assert is_voice_ref(value)


@pytest.mark.parametrize(
    "value",
    [
        "kokoro:",
        "kokoro:/af_heart",
        "pocket:english_2026-04/",
        "unknown:model/voice",
        "kokoro:model/voice/extra",
        "kokoro:model:extra/voice",
    ],
)
def test_reject_invalid_voice_refs(value: str) -> None:
    assert not is_voice_ref(value)
    with pytest.raises(ValueError):
        parse_voice_ref(value)


def test_only_voice_ref_system_is_normalized() -> None:
    reference = parse_voice_ref("KOKORO:V1.0/Af_Heart")
    assert reference == VoiceRef("kokoro", "V1.0", "Af_Heart")
    assert reference.value == "kokoro:V1.0/Af_Heart"


def test_public_system_and_engine_mappings() -> None:
    assert engine_for_public_system("KoKoRo") == "kokoro"
    assert engine_for_public_system("piper") == "piper"
    assert engine_for_public_system("pocket") == "pocket"
    assert engine_for_public_system("kitten") == "kitten"
    assert public_system_for_engine("kokoro") == "kokoro"
    assert public_system_for_engine("pykokoro") == "kokoro"
    assert public_system_for_engine("pipersynth") == "piper"
    assert public_system_for_engine("pocket") == "pocket"
    assert public_system_for_engine("kittensynth") == "kitten"
    assert engine_for_public_system("inflect") == "inflect"
    assert public_system_for_engine("inflect") == "inflect"
    assert public_system_for_engine("inflectsynth") == "inflect"


@pytest.mark.parametrize("system", ["", "unknown", "inflectsynth"])
def test_reject_unsupported_system(system: str) -> None:
    with pytest.raises(ValueError, match="unsupported voice reference system"):
        VoiceRef(system, "target")


def test_reject_empty_target_and_voice_ids() -> None:
    with pytest.raises(ValueError, match="target ID"):
        VoiceRef("piper", " ")
    with pytest.raises(ValueError, match="voice ID"):
        VoiceRef("kokoro", "v1.0", " ")
