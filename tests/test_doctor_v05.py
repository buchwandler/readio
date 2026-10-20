from __future__ import annotations

from readio import doctor


def test_doctor_reports_specialist_versions_and_public_engine_status(monkeypatch) -> None:
    versions = {
        "ssmdconvert": "0.2.0",
        "utterplan": "0.6.3",
        "voicerender": "0.2.0",
        "audiocompose": "0.3.0",
        "audioexport": "0.1.2",
    }
    monkeypatch.setattr(doctor.metadata, "version", versions.__getitem__)
    monkeypatch.setattr(
        doctor.voicerender,
        "list_engines",
        lambda: [{"id": "inflect", "installed": True, "installed_version": "0.1.2"}],
    )
    monkeypatch.setattr(
        doctor.voicerender,
        "engine_status",
        lambda engine: {
            "distribution_version": "0.1.2",
            "status": "ready",
            "compatible": True,
        },
    )

    report = doctor.doctor_report()

    assert report["packages"] == versions
    assert report["engines"] == [
        {
            "id": "inflect",
            "installed": True,
            "version": "0.1.2",
            "status": "ready",
            "compatible": True,
        }
    ]
    text = doctor.format_doctor_report(report)
    assert f"readio: {doctor.__version__}" in text
    assert "ssmdconvert: 0.2.0" in text
    assert "inflect: 0.1.2 (ready)" in text


def test_doctor_recommends_voice_render_extra_for_missing_engine(monkeypatch) -> None:
    monkeypatch.setattr(doctor.metadata, "version", lambda package: package)
    monkeypatch.setattr(
        doctor.voicerender,
        "list_engines",
        lambda: [{"id": "pocket", "installed": False}],
    )
    monkeypatch.setattr(
        doctor.voicerender,
        "engine_status",
        lambda _engine: {"status": "not_installed"},
    )

    text = doctor.format_doctor_report()

    assert 'pip install "voicerender[pocket]"' in text


def test_doctor_reports_missing_optional_audioexport(monkeypatch) -> None:
    def version(package: str) -> str:
        if package == "audioexport":
            raise doctor.metadata.PackageNotFoundError(package)
        return "1.0"

    monkeypatch.setattr(doctor.metadata, "version", version)
    monkeypatch.setattr(doctor.voicerender, "list_engines", list)

    report = doctor.doctor_report()

    assert report["packages"]["audioexport"] == "not installed"
