from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from readio.errors import ReadioError
from readio.integrations import audioexport


def test_audioexport_preflight_uses_public_doctor_and_requested_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, Any] = {}
    report = {"requested_formats_ready": True}
    package = SimpleNamespace(
        FORMATS={"mp3": SimpleNamespace(extension=".mp3")},
        doctor=lambda **kwargs: calls.update(kwargs) or report,
    )
    monkeypatch.setattr(audioexport, "_audioexport_api", lambda: package)

    result = audioexport.preflight_export(
        "mp3",
        ffmpeg=Path("tools/ffmpeg"),
        ffprobe=Path("tools/ffprobe"),
    )

    assert result is report
    assert calls == {
        "ffmpeg": Path("tools/ffmpeg"),
        "ffprobe": Path("tools/ffprobe"),
        "requested_formats": ["mp3"],
    }


def test_audioexport_preflight_reports_missing_tools_and_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = {
        "requested_formats_ready": False,
        "tools": {
            "ffmpeg": {"available": False, "error": "ffmpeg executable not found"},
            "ffprobe": {"available": True},
        },
        "formats": {"m4b": {"available": False, "reason": "AAC encoder unavailable"}},
    }
    package = SimpleNamespace(
        FORMATS={"m4b": SimpleNamespace(extension=".m4b")},
        doctor=lambda **_kwargs: report,
    )
    monkeypatch.setattr(audioexport, "_audioexport_api", lambda: package)

    with pytest.raises(
        ReadioError, match="ffmpeg executable not found.*AAC encoder unavailable"
    ) as error:
        audioexport.preflight_export("m4b")
    assert "--ffmpeg" in str(error.value)
    assert "--ffprobe" in str(error.value)


def test_audioexport_preflight_rejects_unknown_output_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = SimpleNamespace(FORMATS={"mp3": SimpleNamespace(extension=".mp3")})
    monkeypatch.setattr(audioexport, "_audioexport_api", lambda: package)

    with pytest.raises(ReadioError, match="does not support output format 'aac'"):
        audioexport.preflight_export("aac")
