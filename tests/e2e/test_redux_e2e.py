"""Opt-in real-model smoke test for the shared Readio Redux benchmark runner."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(
    os.environ.get("READIO_E2E_REDUX") != "1",
    reason="real audio/Redux E2E test is opt-in",
)
def test_redux_single_voice_e2e(tmp_path: Path) -> None:
    from benchmarks.redux.common import run_default_e2e

    result = run_default_e2e(tmp_path)

    assert result.verification is not None
    assert result.verification.status != "fail"
    assert result.wav is not None
    assert Path(result.wav).is_file()
