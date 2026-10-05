from __future__ import annotations

import json
from dataclasses import asdict
from types import ModuleType
from typing import Any

import pytest

from readio.engines import api_probe
from readio.engines.api_probe import EngineApiProbe, probe_public_api
from readio.engines.kittensynth import KittenSynthEngineAdapter
from readio.engines.pipersynth import PiperSynthEngineAdapter
from readio.engines.pocketsynth import PocketSynthEngineAdapter
from readio.engines.pykokoro import PyKokoroEngineAdapter
from readio.engines.supertonicsynth import SupertonicSynthEngineAdapter


class _LazyModule(ModuleType):
    def __init__(self, name: str, failures: dict[str, Exception]) -> None:
        super().__init__(name)
        self.failures = failures
        self.__version__ = "1.0"
        self.__file__ = "/fake/engine/__init__.py"

    def __getattr__(self, name: str) -> Any:
        failure = self.failures.get(name)
        if failure is not None:
            raise failure
        raise AttributeError(name)


def _module(**values: Any) -> ModuleType:
    module = ModuleType("fake_engine")
    module.__version__ = "1.0"
    module.__file__ = "/fake/engine/__init__.py"
    for name, value in values.items():
        setattr(module, name, value)
    return module


def _probe(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType | None,
    *,
    import_error: Exception | None = None,
    distribution_version: str | None = "1.0",
    **overrides: Any,
) -> EngineApiProbe:
    def metadata_version(_package: str) -> str:
        if distribution_version is None:
            raise api_probe.importlib.metadata.PackageNotFoundError("fake_engine")
        return distribution_version

    def import_module(_package: str) -> ModuleType:
        if import_error is not None:
            raise import_error
        assert module is not None
        return module

    monkeypatch.setattr(api_probe.importlib.metadata, "version", metadata_version)
    monkeypatch.setattr(api_probe.importlib, "import_module", import_module)
    arguments = {
        "engine": "fake",
        "package": "fake_engine",
        "required_symbols": (),
        "required_methods": {},
        **overrides,
    }
    return probe_public_api(**arguments)


def test_package_root_absence_is_distinguished(monkeypatch: pytest.MonkeyPatch) -> None:
    error = ModuleNotFoundError("No module named 'fake_engine'")
    error.name = "fake_engine"
    result = _probe(monkeypatch, None, import_error=error, distribution_version=None)

    assert result.status == "package_missing"
    assert result.compatible is False
    assert result.failed_stage == "module_import"
    assert result.error_type == "ModuleNotFoundError"


def test_transitive_module_not_found_is_a_probe_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    error = ModuleNotFoundError("No module named 'transitive_dependency'")
    error.name = "transitive_dependency"
    result = _probe(monkeypatch, None, import_error=error)

    assert result.status == "api_probe_failed"
    assert result.failed_stage == "module_import"
    assert result.error_message == str(error)


def test_missing_required_symbol_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _probe(monkeypatch, _module(), required_symbols=("SynthesisRequest",))

    assert result.status == "api_incompatible"
    assert result.missing_symbols == ("SynthesisRequest",)
    assert result.failed_stage is None


@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (ImportError("lazy dependency failed"), "ImportError"),
        (RuntimeError("lazy import exploded"), "RuntimeError"),
    ],
)
def test_lazy_symbol_exception_is_preserved(
    monkeypatch: pytest.MonkeyPatch, error: Exception, error_type: str
) -> None:
    module = _LazyModule("fake_engine", {"PublicType": error})
    result = _probe(monkeypatch, module, required_symbols=("PublicType",))

    assert result.status == "api_probe_failed"
    assert result.failed_stage == "symbol_resolution"
    assert result.failed_symbol == "PublicType"
    assert result.error_type == error_type
    assert result.error_message == str(error)


def test_missing_and_non_callable_methods_are_reported_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PublicType:
        present = "not callable"

    result = _probe(
        monkeypatch,
        _module(PublicType=PublicType),
        required_symbols=("PublicType",),
        required_methods={"PublicType": ("absent", "present")},
    )

    assert result.status == "api_incompatible"
    assert result.missing_methods == ("PublicType.absent", "PublicType.present")


def test_explicit_contract_version_match_and_contract_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def request_api_contract() -> dict[str, int]:
        calls.append("called")
        return {"request_api_version": 1}

    result = _probe(
        monkeypatch,
        _module(
            SynthesisRequest=object(),
            REQUEST_API_VERSION=1,
            request_api_contract=request_api_contract,
        ),
        expected_api_version=1,
        required_symbols=("SynthesisRequest",),
    )

    assert result.status == "ready"
    assert result.compatible is True
    assert result.api_version == 1
    assert result.expected_api_version == 1
    assert result.contract_source == "explicit"
    assert calls == ["called"]


def test_unsupported_explicit_contract_version_is_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _probe(
        monkeypatch,
        _module(REQUEST_API_VERSION=2, request_api_contract=dict),
        expected_api_version=1,
    )

    assert result.status == "api_version_incompatible"
    assert result.compatible is False
    assert result.api_version == 2
    assert result.expected_api_version == 1
    assert result.contract_source == "explicit"


def test_request_api_contract_exception_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_contract() -> None:
        raise RuntimeError("contract introspection failed")

    result = _probe(
        monkeypatch,
        _module(REQUEST_API_VERSION=1, request_api_contract=fail_contract),
        expected_api_version=1,
    )

    assert result.status == "api_probe_failed"
    assert result.failed_stage == "contract_inspection"
    assert result.failed_symbol == "request_api_contract"
    assert result.error_type == "RuntimeError"
    assert result.error_message == "contract introspection failed"


def test_required_explicit_contract_reports_missing_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _probe(
        monkeypatch,
        _module(),
        required_symbols=("SynthesisRequest",),
        expected_api_version=1,
        require_explicit_contract=True,
    )

    assert result.status == "api_incompatible"
    assert result.missing_symbols == (
        "SynthesisRequest",
        "REQUEST_API_VERSION",
        "request_api_contract",
    )


def test_incomplete_optional_contract_falls_back_to_legacy_symbols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _probe(
        monkeypatch,
        _module(SynthesisRequest=object(), REQUEST_API_VERSION=1),
        required_symbols=("SynthesisRequest",),
    )

    assert result.status == "ready"
    assert result.contract_source == "legacy_symbols"
    assert result.warnings == (
        "Incomplete explicit request API contract; using legacy symbol checks.",
    )


def test_legacy_symbols_are_accepted_when_contract_is_not_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _probe(
        monkeypatch,
        _module(SynthesisRequest=object()),
        required_symbols=("SynthesisRequest",),
    )

    assert result.status == "ready"
    assert result.contract_source == "legacy_symbols"


def test_distribution_module_version_mismatch_is_warning_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _probe(
        monkeypatch,
        _module(SynthesisRequest=object()),
        distribution_version="1.1",
        required_symbols=("SynthesisRequest",),
    )

    assert result.status == "ready"
    assert result.compatible is True
    assert result.distribution_version == "1.1"
    assert result.module_version == "1.0"
    assert result.module_path == "/fake/engine/__init__.py"
    assert "Imported module version differs from distribution metadata." in result.warnings


def test_module_root_function_callability_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _probe(
        monkeypatch,
        _module(discover_models="not callable"),
        required_symbols=("discover_models",),
        required_methods={"__module__": ("discover_models",)},
    )

    assert result.status == "api_incompatible"
    assert result.missing_methods == ("discover_models",)


def test_probe_result_is_safe_for_json_serialization(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _probe(
        monkeypatch,
        _module(REQUEST_API_VERSION=1, request_api_contract=dict),
        expected_api_version=1,
    )

    serialized = json.dumps(asdict(result))
    assert '"status": "ready"' in serialized
    assert '"contract_source": "explicit"' in serialized


def test_probe_does_not_call_runtime_or_synthesis_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Runtime:
        @classmethod
        def open(cls) -> None:
            calls.append("open")

        def synthesize(self) -> None:
            calls.append("synthesize")

    result = _probe(
        monkeypatch,
        _module(Runtime=Runtime),
        required_symbols=("Runtime",),
        required_methods={"Runtime": ("open", "synthesize")},
    )

    assert result.status == "ready"
    assert calls == []


@pytest.mark.parametrize(
    ("adapter_type", "symbols", "methods"),
    [
        (
            PyKokoroEngineAdapter,
            (
                "KokoroSynthesizer",
                "SynthesisConfig",
                "SynthesisRequest",
                "PronunciationOverride",
                "LinguisticToken",
                "VoiceLevelConfig",
                "SynthesisInputTooLongError",
                "ShortSentenceConfig",
            ),
            {"KokoroSynthesizer": ("prepare", "synthesize", "close")},
        ),
        (
            PiperSynthEngineAdapter,
            (
                "PiperVoice",
                "VoiceAssetManager",
                "SynthesisRequest",
                "SynthesisResult",
                "SynthesisConfig",
                "LinguisticToken",
                "PronunciationOverride",
                "VoiceLevelConfig",
                "SynthesisInputTooLongError",
            ),
            {
                "PiperVoice": ("from_pretrained", "synthesize", "close"),
                "VoiceAssetManager": ("list_voices", "get_voice_metadata"),
            },
        ),
        (
            PocketSynthEngineAdapter,
            (
                "PocketRuntime",
                "SynthesisRequest",
                "SynthesisResult",
                "RequestMeasure",
                "GenerationConfig",
                "VoiceLevelConfig",
                "SynthesisInputTooLongError",
                "BundleAssetManager",
                "VoicePromptInfo",
                "PreparedVoice",
                "inspect_voice_prompt",
                "list_voice_prompts",
                "discover_bundles",
                "runtime_identity",
            ),
            {
                "PocketRuntime": (
                    "from_resolved",
                    "prepare_voice",
                    "synthesize",
                    "close",
                    "measure_request",
                ),
                "BundleAssetManager": ("list_bundles", "resolve_bundle"),
                "__module__": (
                    "inspect_voice_prompt",
                    "list_voice_prompts",
                    "discover_bundles",
                    "runtime_identity",
                ),
            },
        ),
        (
            KittenSynthEngineAdapter,
            (
                "KittenVoice",
                "SynthesisConfig",
                "SynthesisResult",
                "discover_models",
                "runtime_identity",
            ),
            {
                "KittenVoice": ("synthesize_prepared", "from_pretrained", "from_local", "close"),
                "__module__": ("discover_models", "runtime_identity"),
            },
        ),
        (
            SupertonicSynthEngineAdapter,
            (
                "SupertonicRuntime",
                "SynthesisRequest",
                "GenerationConfig",
                "AtomicSynthesisResult",
                "RequestMeasure",
                "SynthesisInputTooLongError",
                "VoiceLevelConfig",
                "DiscoveredModel",
                "discover_models",
                "runtime_identity",
            ),
            {
                "SupertonicRuntime": ("from_pretrained", "measure_request", "synthesize", "close"),
                "__module__": ("discover_models", "runtime_identity"),
            },
        ),
    ],
)
def test_each_adapter_accepts_its_public_request_contract(
    monkeypatch: pytest.MonkeyPatch,
    adapter_type: type[Any],
    symbols: tuple[str, ...],
    methods: dict[str, tuple[str, ...]],
) -> None:
    module = _module()
    for symbol in symbols:
        setattr(module, symbol, object())
    for owner_name, method_names in methods.items():
        if owner_name in {"__module__", "module"}:
            owner = module
        else:
            owner = type(
                owner_name,
                (),
                {method_name: lambda *args, **kwargs: None for method_name in method_names},
            )
            setattr(module, owner_name, owner)
        for method_name in method_names:
            if owner_name in {"__module__", "module"}:
                setattr(module, method_name, lambda *args, **kwargs: None)
    module.REQUEST_API_VERSION = 1
    module.request_api_contract = dict

    result = _probe(monkeypatch, module)
    probe = adapter_type().probe_api()

    assert result.status == "ready"
    assert probe.compatible is True, probe
    assert probe.status == "ready"
    assert probe.api_version == 1
    assert probe.contract_source == "explicit"


def test_explicit_contract_enforces_additional_public_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module(
        LegacyRuntime=type("LegacyRuntime", (), {"render": lambda self: None}),
        REQUEST_API_VERSION=1,
        request_api_contract=dict,
    )
    result = _probe(
        monkeypatch,
        module,
        required_symbols=("LegacyRuntime",),
        required_methods={"LegacyRuntime": ("render",)},
        expected_api_version=1,
        contract_required_symbols=("RequestMeasure",),
        contract_required_methods={"LegacyRuntime": ("measure_request",)},
        legacy_required_methods={"LegacyRuntime": ("measure_legacy",)},
    )

    assert result.status == "api_incompatible"
    assert result.missing_symbols == ("RequestMeasure",)
    assert result.missing_methods == ("LegacyRuntime.measure_request",)


def test_legacy_contract_uses_legacy_only_requirements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module(LegacyRuntime=type("LegacyRuntime", (), {"measure_legacy": lambda self: None}))
    result = _probe(
        monkeypatch,
        module,
        required_symbols=("LegacyRuntime",),
        required_methods={"LegacyRuntime": ()},
        contract_required_symbols=("RequestMeasure",),
        contract_required_methods={"LegacyRuntime": ("measure_request",)},
        legacy_required_methods={"LegacyRuntime": ("measure_legacy",)},
    )

    assert result.status == "ready"
    assert result.contract_source == "legacy_symbols"


def test_probe_emits_compact_structured_debug_event(caplog, monkeypatch) -> None:
    import logging

    module = _module(SynthesisRequest=object())
    with caplog.at_level(logging.DEBUG, logger=api_probe.__name__):
        result = _probe(monkeypatch, module, required_symbols=("SynthesisRequest",))

    events = [
        record.getMessage()
        for record in caplog.records
        if "engine.api_probe " in record.getMessage()
    ]
    assert len(events) == 1
    payload = json.loads(events[0].split(" ", 1)[1])
    assert payload["status"] == "ready"
    assert payload["engine"] == result.engine
    assert payload["distribution_version"] == result.distribution_version


def test_probe_failure_logs_captured_traceback_at_debug(caplog, monkeypatch) -> None:
    import logging

    module = _LazyModule("fake_engine", {"LazySymbol": ImportError("transitive import failed")})
    with caplog.at_level(logging.DEBUG, logger=api_probe.__name__):
        result = _probe(monkeypatch, module, required_symbols=("LazySymbol",))

    failure_logs = [
        record for record in caplog.records if "public symbol LazySymbol" in record.getMessage()
    ]
    assert result.status == "api_probe_failed"
    assert len(failure_logs) == 1
    assert failure_logs[0].exc_info is not None
