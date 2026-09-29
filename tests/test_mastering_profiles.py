from __future__ import annotations

import pytest

from readio.plan import CompositionOptions, CompositionPlanV2, resolve_mastering_policy


@pytest.mark.parametrize(
    ("profile", "target_lufs", "ceiling"),
    [
        ("spoken-word", -16.0, -1.0),
        ("spoken-word-dual-mono", -19.0, -1.0),
        ("broadcast-ebu", -23.0, -1.0),
        ("peak-safe", None, -1.0),
        ("off", None, None),
    ],
)
def test_mastering_profile_defaults(profile, target_lufs, ceiling):
    resolved = resolve_mastering_policy(profile)
    assert resolved.profile == profile
    assert resolved.target_lufs == target_lufs
    assert resolved.true_peak_ceiling_dbtp == ceiling
    assert resolved.collect_metrics is (profile != "off")


def test_public_composition_options_default_to_spoken_word():
    options = CompositionOptions()
    resolved = resolve_mastering_policy(
        options.mastering,
        target_lufs=options.target_lufs,
        true_peak_ceiling_dbtp=options.true_peak_ceiling_dbtp,
        peak_policy=options.peak_policy,
    )
    assert options.mastering == "spoken-word"
    assert resolved.target_lufs == -16.0
    assert resolved.true_peak_ceiling_dbtp == -1.0
    assert CompositionPlanV2().to_dict()["target_lufs"] == -16.0


def test_numeric_overrides_replace_profile_defaults_and_none_inherits():
    resolved = resolve_mastering_policy(
        "spoken-word-dual-mono", target_lufs=-18.0, true_peak_ceiling_dbtp=-2.0
    )
    assert resolved.target_lufs == -18.0
    assert resolved.true_peak_ceiling_dbtp == -2.0
    assert resolve_mastering_policy("broadcast-ebu", target_lufs=None).target_lufs == -23.0


def test_off_only_skips_analysis_when_no_numeric_override_is_given():
    disabled = resolve_mastering_policy("off")
    overridden = resolve_mastering_policy("off", target_lufs=-20.0)
    assert disabled.collect_metrics is False
    assert overridden.target_lufs == -20.0
    assert overridden.true_peak_ceiling_dbtp is None
    assert overridden.collect_metrics is True


def test_unknown_or_nonfinite_mastering_inputs_are_rejected():
    with pytest.raises(ValueError, match="unsupported mastering profile"):
        CompositionOptions(mastering="acx")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite number"):
        CompositionOptions(target_lufs=float("nan"))
