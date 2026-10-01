from __future__ import annotations

from readio.doctor import check_ssmdconvert


def test_doctor_reports_ssmdconvert_as_a_core_dependency() -> None:
    status = check_ssmdconvert()
    assert status["available"] is True
    assert status["version"] is not None
