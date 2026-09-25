from __future__ import annotations

from pathlib import Path

import pytest

from readio.config import ReadioConfig
from readio.errors import SSMDInputError
from readio.ssmd_authoring import materialize_voice_bindings, roundtrip_check


def test_materialize_rejects_legacy_source_before_writing(tmp_path: Path) -> None:
    source = tmp_path / "legacy.ssmd"
    output = tmp_path / "output" / "materialized.ssmd"
    source.write_text('<div voice="host">Hello.</div>', encoding="utf-8")

    with pytest.raises(SSMDInputError) as error:
        materialize_voice_bindings(source, {"host": "af_sarah"}, provider="kokoro", output=output)

    assert error.value.source_path == source
    assert error.value.details["diagnostics"][0]["code"] == "syntax.legacy_div_directive"
    assert not output.exists()


def test_materialize_adds_ssmd_09_version_to_generated_header(tmp_path: Path) -> None:
    source = tmp_path / "source.ssmd"
    output = tmp_path / "materialized.ssmd"
    source.write_text('[Hello.]{voice="host"}', encoding="utf-8")

    materialize_voice_bindings(source, {"host": "af_sarah"}, provider="kokoro", output=output)

    assert output.read_text(encoding="utf-8").startswith(
        "---\nssmd_version: '0.9'\nvoice_bindings:\n"
    )


def test_roundtrip_lints_original_source_with_strict_dialect(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.ssmd"
    source.write_text('[Hello.]{voice="host"}', encoding="utf-8")
    captured: dict[str, object] = {}
    monkeypatch.setattr("readio.ssmd_authoring.executable", lambda: "ssmd")

    def run(args, *, config_path):
        captured["args"] = list(args)
        captured["config_path"] = config_path
        return {"ok": True}

    monkeypatch.setattr("readio.ssmd_authoring.run_ssmd_json", run)

    result = roundtrip_check(source, ReadioConfig())

    assert result == {"ok": True}
    assert captured["args"] == [
        "lint",
        str(source),
        "--dialect",
        "0.9",
        "--voice-provider",
        "kokoro",
        "--fail-on-warn",
        "--roundtrip",
    ]
    assert not Path(captured["config_path"]).exists()


def test_roundtrip_rejects_legacy_before_calling_ssmd(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "legacy.ssmd"
    source.write_text('<div voice="host">Hello.</div>', encoding="utf-8")
    monkeypatch.setattr(
        "readio.ssmd_authoring.run_ssmd_json", lambda *args, **kwargs: pytest.fail()
    )

    with pytest.raises(SSMDInputError):
        roundtrip_check(source, ReadioConfig())
