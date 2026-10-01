from __future__ import annotations

from readio.role_targets import VoiceTarget, voice_target_from_mapping


def test_voice_target_serialization_keeps_only_structured_identity() -> None:
    target = voice_target_from_mapping(
        {
            "engine": "kokoro",
            "target_id": "v1.0",
            "voice": "af_heart",
            "selector": "legacy-selector-value",
        }
    )

    assert target == VoiceTarget("pykokoro", "af_heart", target_id="v1.0")
    assert target.to_dict() == {
        "engine": "pykokoro",
        "voice": "af_heart",
        "target_id": "v1.0",
    }


def test_voice_target_serialization_omits_absent_target_id() -> None:
    assert VoiceTarget("piper", "en_US-amy-medium").to_dict() == {
        "engine": "piper",
        "voice": "en_US-amy-medium",
    }
