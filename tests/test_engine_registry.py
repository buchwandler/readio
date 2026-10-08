"""Tests for the engine registry and engine ID normalization.

These tests verify:
- ENGINE_ALIASES and normalize_engine_id()
- Registry facade APIs (get_engine, iter_engines, engine_ids, default_engine)
- Optional engine diagnostics
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from readio.engines.api_probe import EngineApiProbe
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

    def test_canonical_kokoro_unchanged(self) -> None:
        assert normalize_engine_id("kokoro") == "kokoro"

    def test_pykokoro_alias_normalizes_to_kokoro(self) -> None:
        assert normalize_engine_id("pykokoro") == "kokoro"

    def test_alias_pipersynth_normalizes_to_piper(self) -> None:
        assert normalize_engine_id("pipersynth") == "piper"

    def test_alias_kittensynth_normalizes_to_kitten(self) -> None:
        assert normalize_engine_id("kittensynth") == "kitten"

    def test_alias_supertonicsynth_normalizes_to_supertonic(self) -> None:
        assert normalize_engine_id("supertonicsynth") == "supertonic"

    def test_inflectsynth_alias_normalizes_to_inflect(self) -> None:
        assert normalize_engine_id("inflectsynth") == "inflect"

    def test_unknown_engine_passes_through(self) -> None:
        assert normalize_engine_id("unknown") == "unknown"

    def test_empty_string_passes_through(self) -> None:
        assert normalize_engine_id("") == ""


class TestEngineAliases:
    """Tests for ENGINE_ALIASES dict."""

    def test_aliases_are_input_only(self) -> None:
        assert ENGINE_ALIASES == {
            "pykokoro": "kokoro",
            "pipersynth": "piper",
            "kittensynth": "kitten",
            "supertonicsynth": "supertonic",
            "inflectsynth": "inflect",
        }

    def test_canonical_ids(self) -> None:
        assert CANONICAL_ENGINE_IDS == frozenset(
            {"kokoro", "piper", "pocket", "supertonic", "kitten", "inflect"}
        )


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

    def test_get_engine_normalizes_inflectsynth_alias(self) -> None:
        adapter = type("MockAdapter", (), {"id": "inflect"})()
        registry = EngineRegistry()
        registry.register(adapter)
        with patch("readio.engines.registry._registry", registry):
            assert get_engine("inflectsynth") is adapter

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

    def test_default_engine_returns_kokoro(self) -> None:
        """default_engine should return the canonical kokoro adapter."""
        mock_adapter = type(
            "MockAdapter",
            (),
            {
                "id": "kokoro",
                "version": lambda self: "0.10.0",
            },
        )()

        registry = EngineRegistry()
        registry.register(mock_adapter)

        with patch("readio.engines.registry._registry", registry):
            result = default_engine()
            assert result.id == "kokoro"

    def test_iter_engines_yields_registered(self) -> None:
        """iter_engines should yield all registered adapters."""
        mock_piper = type("MockPiper", (), {"id": "piper", "version": lambda self: "0.1.0"})()
        mock_kokoro = type("MockKokoro", (), {"id": "kokoro", "version": lambda self: "0.10.0"})()

        registry = EngineRegistry()
        registry.register(mock_piper)
        registry.register(mock_kokoro)

        with patch("readio.engines.registry._registry", registry):
            adapters = list(iter_engines())
            ids = {a.id for a in adapters}
            assert ids == {"piper", "kokoro"}


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
        # Known optional engines remain visible when their packages are absent.
        assert {"kokoro", "piper", "pocket", "supertonic", "kitten", "inflect"}.issubset(status)

    def test_inflect_registers_lazily_and_reports_missing_package(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "inflectsynth", None)
        monkeypatch.setattr("readio.engines.registry.CANONICAL_ENGINE_IDS", frozenset({"inflect"}))
        registry = EngineRegistry()

        adapter = registry.get("inflect")
        assert adapter is not None
        assert adapter.id == "inflect"
        status = registry.status()["inflect"]
        assert status["adapter"] is True
        assert status["package"] is False
        assert status["status"] == "package_missing"

    def test_inflect_installed_package_status_uses_probe_result(self, monkeypatch) -> None:
        monkeypatch.setattr("readio.engines.registry.CANONICAL_ENGINE_IDS", frozenset({"inflect"}))
        registry = EngineRegistry()
        adapter = registry.get("inflect")
        assert adapter is not None
        probe = EngineApiProbe(
            engine="inflect",
            package="inflectsynth",
            compatible=True,
            status="ready",
            distribution_version="0.1.1",
            module_version="0.1.1",
            module_path="/fake/inflectsynth/__init__.py",
            expected_api_version=1,
            api_version=1,
            contract_source="explicit",
        )
        monkeypatch.setattr(adapter, "probe_api", lambda: probe)

        status = registry.status()["inflect"]
        assert status["adapter"] is True
        assert status["package"] is True
        assert status["version"] == "0.1.1"
        assert status["status"] == "ready"

    def test_status_reuses_one_structured_probe_result(self, monkeypatch) -> None:
        registry = EngineRegistry()
        probe_calls = 0

        class Adapter:
            id = "piper"
            package_name = "pipersynth"

            def probe_api(self):
                nonlocal probe_calls
                probe_calls += 1
                return EngineApiProbe(
                    engine=self.id,
                    package=self.package_name,
                    compatible=False,
                    status="api_incompatible",
                    distribution_version="0.2.1",
                    module_version="0.2.0",
                    module_path="/tmp/pipersynth/__init__.py",
                    expected_api_version=1,
                    missing_methods=("PiperVoice.close",),
                )

        adapter = Adapter()
        registry.register(adapter)
        monkeypatch.setattr(registry, "get", lambda engine: adapter if engine == "piper" else None)

        status = registry.status()["piper"]

        assert probe_calls == 1
        assert status["adapter"] is True
        assert status["package"] is True
        assert status["version"] == "0.2.1"
        assert status["module_version"] == "0.2.0"
        assert status["api_compatible"] is False
        assert status["status"] == "api_incompatible"
        assert status["missing_methods"] == ("PiperVoice.close",)

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
        assert engine_for_ssmd_provider("kokoro") == "kokoro"
        assert engine_for_ssmd_provider("piper") == "piper"

    def test_engine_for_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="No synthesis engine is registered"):
            engine_for_ssmd_provider("unknown")

    def test_ssmd_provider_comes_from_adapter_capabilities(self, monkeypatch) -> None:
        capabilities = type("Capabilities", (), {"voice_binding_namespace": "piper"})()
        adapter = type("MockAdapter", (), {"capabilities": lambda self: capabilities})()
        requested = []
        monkeypatch.setattr(
            "readio.engines.registry.get_engine",
            lambda engine: requested.append(engine) or adapter,
        )

        assert ssmd_provider_for_engine("pipersynth") == "piper"
        assert requested == ["piper"]
