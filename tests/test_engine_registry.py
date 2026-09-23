"""Tests for the engine registry and engine ID normalization.

These tests verify:
- ENGINE_ALIASES and normalize_engine_id()
- Registry facade APIs (get_engine, iter_engines, engine_ids, default_engine)
- Optional engine diagnostics
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from readio.engines.registry import (
    CANONICAL_ENGINE_IDS,
    ENGINE_ALIASES,
    EngineRegistry,
    default_engine,
    engine_for_ssmd_provider,
    engine_ids,
    get_engine,
    iter_engines,
    normalize_engine_id,
    ssmd_provider_for_engine,
)

# ---------------------------------------------------------------------------
# Alias normalization tests
# ---------------------------------------------------------------------------


class TestNormalizeEngineId:
    """Tests for normalize_engine_id()."""

    def test_canonical_piper_unchanged(self) -> None:
        assert normalize_engine_id("piper") == "piper"

    def test_canonical_pykokoro_unchanged(self) -> None:
        assert normalize_engine_id("pykokoro") == "pykokoro"

    def test_alias_pipersynth_normalizes_to_piper(self) -> None:
        assert normalize_engine_id("pipersynth") == "piper"

    def test_alias_kokoro_normalizes_to_pykokoro(self) -> None:
        assert normalize_engine_id("kokoro") == "pykokoro"

    def test_unknown_engine_passes_through(self) -> None:
        assert normalize_engine_id("unknown") == "unknown"

    def test_empty_string_passes_through(self) -> None:
        assert normalize_engine_id("") == ""


class TestEngineAliases:
    """Tests for ENGINE_ALIASES dict."""

    def test_aliases_contains_pipersynth(self) -> None:
        assert ENGINE_ALIASES["pipersynth"] == "piper"

    def test_aliases_contains_kokoro(self) -> None:
        assert ENGINE_ALIASES["kokoro"] == "pykokoro"

    def test_canonical_ids(self) -> None:
        assert "pykokoro" in CANONICAL_ENGINE_IDS
        assert "piper" in CANONICAL_ENGINE_IDS


# ---------------------------------------------------------------------------
# Registry facade tests
# ---------------------------------------------------------------------------


class TestRegistryFacade:
    """Tests for the module-level registry facade APIs."""

    def test_get_engine_normalizes_alias(self) -> None:
        """get_engine('pipersynth') should resolve to the piper adapter."""
        # Create a mock adapter
        mock_adapter = type(
            "MockAdapter",
            (),
            {
                "id": "piper",
                "version": lambda self: "0.1.0",
                "capabilities": lambda self: None,
            },
        )()

        registry = EngineRegistry()
        registry.register(mock_adapter)

        with patch("readio.engines.registry._registry", registry):
            result = get_engine("pipersynth")
            assert result is mock_adapter

    def test_get_engine_raises_for_missing(self) -> None:
        """get_engine should raise ValueError for unavailable engines."""
        registry = EngineRegistry()

        with (
            patch("readio.engines.registry._registry", registry),
            pytest.raises(ValueError, match="not available"),
        ):
            get_engine("nonexistent")

    def test_engine_ids_returns_registered(self) -> None:
        """engine_ids should return canonical IDs of registered engines."""
        mock_adapter = type(
            "MockAdapter",
            (),
            {
                "id": "piper",
                "version": lambda self: "0.1.0",
            },
        )()

        registry = EngineRegistry()
        registry.register(mock_adapter)

        with patch("readio.engines.registry._registry", registry):
            result = engine_ids()
            assert "piper" in result

    def test_default_engine_returns_pykokoro(self) -> None:
        """default_engine should return the pykokoro adapter."""
        mock_adapter = type(
            "MockAdapter",
            (),
            {
                "id": "pykokoro",
                "version": lambda self: "0.9.0",
            },
        )()

        registry = EngineRegistry()
        registry.register(mock_adapter)

        with patch("readio.engines.registry._registry", registry):
            result = default_engine()
            assert result.id == "pykokoro"

    def test_iter_engines_yields_registered(self) -> None:
        """iter_engines should yield all registered adapters."""
        mock_piper = type("MockPiper", (), {"id": "piper", "version": lambda self: "0.1.0"})()
        mock_pykokoro = type(
            "MockPykokoro", (), {"id": "pykokoro", "version": lambda self: "0.9.0"}
        )()

        registry = EngineRegistry()
        registry.register(mock_piper)
        registry.register(mock_pykokoro)

        with patch("readio.engines.registry._registry", registry):
            adapters = list(iter_engines())
            ids = {a.id for a in adapters}
            assert ids == {"piper", "pykokoro"}


# ---------------------------------------------------------------------------
# Registry class tests
# ---------------------------------------------------------------------------


class TestEngineRegistryClass:
    """Tests for the EngineRegistry class."""

    def test_register_and_get(self) -> None:
        registry = EngineRegistry()
        mock_adapter = type("MockAdapter", (), {"id": "test"})()
        registry.register(mock_adapter)
        assert registry.get("test") is mock_adapter

    def test_get_returns_none_for_unknown(self) -> None:
        registry = EngineRegistry()
        assert registry.get("unknown") is None

    def test_available_engines_returns_sorted(self) -> None:
        registry = EngineRegistry()
        a1 = type("A1", (), {"id": "zeta"})()
        a2 = type("A2", (), {"id": "alpha"})()
        registry.register(a1)
        registry.register(a2)
        assert registry.available_engines() == ("alpha", "zeta")

    def test_status_includes_known_engines(self) -> None:
        registry = EngineRegistry()
        status = registry.status()
        # Should include pykokoro and piper even if not registered
        assert "pykokoro" in status
        assert "piper" in status

    def test_iter_adapters_yields_all(self) -> None:
        registry = EngineRegistry()
        a1 = type("A1", (), {"id": "a"})()
        a2 = type("A2", (), {"id": "b"})()
        registry.register(a1)
        registry.register(a2)
        ids = {a.id for a in registry.iter_adapters()}
        assert ids == {"a", "b"}


class TestEngineProviderHelpers:
    def test_engine_for_ssmd_provider(self) -> None:
        assert engine_for_ssmd_provider("kokoro") == "pykokoro"
        assert engine_for_ssmd_provider("piper") == "piper"

    def test_engine_for_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="No synthesis engine is registered"):
            engine_for_ssmd_provider("unknown")

    def test_ssmd_provider_comes_from_adapter_capabilities(self, monkeypatch) -> None:
        capabilities = type("Capabilities", (), {"ssmd_provider": "piper"})()
        adapter = type("MockAdapter", (), {"capabilities": lambda self: capabilities})()
        requested = []
        monkeypatch.setattr(
            "readio.engines.registry.get_engine",
            lambda engine: requested.append(engine) or adapter,
        )

        assert ssmd_provider_for_engine("pipersynth") == "piper"
        assert requested == ["piper"]
