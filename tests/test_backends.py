from __future__ import annotations

import pytest

from readio.backends import default_backend, get_backend, iter_backends
from readio.backends.pykokoro import PyKokoroBackend


def test_pykokoro_is_registered_and_default() -> None:
    assert tuple(backend.id for backend in iter_backends()) == ("pykokoro",)
    assert isinstance(default_backend(), PyKokoroBackend)
    assert get_backend("pykokoro") is default_backend()


def test_unknown_backend_has_stable_error() -> None:
    with pytest.raises(ValueError, match="Unknown synthesis backend 'pipersynth'"):
        get_backend("pipersynth")


def test_backend_metadata_is_explicit() -> None:
    backend = default_backend()
    assert backend.id == "pykokoro"
    assert backend.ssmd_provider == "kokoro"
    assert "model_source" in backend.supported_options
