from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Self

import numpy as np
import pytest

from readio import models
from readio.backends import registry
from readio.backends.base import BackendResolution, DiscoveryInfo
from readio.config import ReadioConfig, with_overrides
from readio.document import InputDocument
from readio.models import ModelInfo, VoiceMetadata, discover_model_info
from readio.plan import (
    InputRequest,
    OutputRequest,
    PlanDiagnostic,
    PlanRequest,
    SynthesisRequest,
    resolve_plan,
)
from readio.reader import render_from_plan
from readio.voices import resolve_voice_selector


class _FakeAudio:
    def __init__(self, sample_rate: int = 24000) -> None:
        self.audio = np.zeros(2400, dtype=np.float32)
        self.sample_rate = sample_rate
        self.markers = ()

    def release_audio(self) -> None:
        return None


class _FakePrepared:
    def __init__(self) -> None:
        self.units = (SimpleNamespace(),)

    def render(self, indices=None):
        yield _FakeAudio()


class _FakeSession:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> bool:
        return False

    @contextmanager
    def prepare_units(self, text: str, *, unit: str):
        yield _FakePrepared()


class FixtureBackend:
    ssmd_provider = "fixture"

    def __init__(
        self, backend_id: str = "fixture", *, supported_options=(), validation=False
    ) -> None:
        self.id = backend_id
        self.supported_options = frozenset(supported_options)
        self.validation = validation
        self.discovery_calls = 0
        self.selections: list[BackendResolution] = []

    def version(self) -> str:
        return "test"

    @property
    def model(self) -> ModelInfo:
        return ModelInfo(
            id="fixture-model",
            source="fixture",
            languages=("en-us",),
            voices=("fixture-voice",),
            default_voice="fixture-voice",
            qualities=("test",),
            g2p_backend=None,
            lexicons=None,
            frontend="fixture",
            status="ready",
            experimental=False,
            runtime_available=True,
            redistribution_allowed=True,
            backend=self.id,
            sample_rate=24000,
            voice_details=(
                VoiceMetadata(
                    id="fixture-voice",
                    gender="unknown",
                    language="en",
                    locale="en-us",
                    language_label="English",
                ),
            ),
        )

    def discover_models(self, **kwargs):
        self.discovery_calls += 1
        return (self.model,), DiscoveryInfo(registry_source="fixture")

    def resolve_defaults(self, candidate):
        return replace(
            candidate,
            model=candidate.model or self.model.id,
            source=candidate.source or self.model.source,
            quality=candidate.quality or self.model.qualities[0],
            voice=candidate.voice or self.model.default_voice,
        ), ()

    def validate_selection(self, selection: BackendResolution):
        self.selections.append(selection)
        if self.validation:
            return (
                PlanDiagnostic(
                    code="fixture.selection_invalid",
                    severity="error",
                    message="fixture rejected this selection",
                    field="synthesis.model",
                ),
            )
        return ()

    @contextmanager
    def open_session(self, plan, document):
        yield _FakeSession()


def _request(synthesis: SynthesisRequest) -> PlanRequest:
    return PlanRequest(
        operation="render",
        input=InputRequest(document=InputDocument(text="Hello.", source_path=None, format="text")),
        synthesis=synthesis,
        output=OutputRequest(),
    )


def test_generic_discovery_delegates_to_fixture_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FixtureBackend()
    monkeypatch.setitem(registry._BACKENDS, backend.id, backend)
    monkeypatch.setattr(
        models,
        "_discover_pykokoro_model_info",
        lambda **kwargs: pytest.fail("generic discovery called PyKokoro"),
    )

    discovered, _ = discover_model_info(backend=backend.id)

    assert discovered == (backend.model,)
    assert backend.discovery_calls == 1


def test_voice_selector_is_scoped_to_selected_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    first = FixtureBackend("fixture-a")
    second = FixtureBackend("fixture-b")
    monkeypatch.setitem(registry._BACKENDS, first.id, first)
    monkeypatch.setitem(registry._BACKENDS, second.id, second)

    resolved = resolve_voice_selector(
        "en-us-1",
        language="en-us",
        model=None,
        source=None,
        engine=first.id,
    )

    assert resolved is not None
    assert resolved.engine == first.id
    assert first.discovery_calls == 1
    assert second.discovery_calls == 0


def test_unsupported_backend_option_fails_before_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FixtureBackend()
    monkeypatch.setitem(registry._BACKENDS, backend.id, backend)
    cfg = with_overrides(ReadioConfig(), engine=backend.id)

    plan = resolve_plan(
        cfg,
        _request(
            SynthesisRequest(
                engine=backend.id,
                language="en-us",
                model=backend.model.id,
                spacy="required",
            )
        ),
    )

    assert not plan.ok
    assert plan.synthesis is None
    assert any(diagnostic.code == "backend.option_unsupported" for diagnostic in plan.diagnostics)
    assert backend.discovery_calls == 0


def test_backend_validation_diagnostics_are_included_in_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FixtureBackend(validation=True)
    monkeypatch.setitem(registry._BACKENDS, backend.id, backend)
    cfg = with_overrides(ReadioConfig(), engine=backend.id)

    plan = resolve_plan(
        cfg,
        _request(SynthesisRequest(engine=backend.id, language="en-us")),
    )

    assert not plan.ok
    assert any(d.code == "fixture.selection_invalid" for d in plan.diagnostics)
    assert backend.selections
    assert backend.selections[0].backend == backend.id


class _Sink:
    def __init__(self) -> None:
        self.chunks: list[tuple[np.ndarray, int]] = []

    def write(self, audio: np.ndarray, sample_rate: int) -> None:
        self.chunks.append((audio, sample_rate))

    def close(self) -> None:
        return None


def test_fixture_backend_renders_through_bounded_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FixtureBackend()
    monkeypatch.setitem(registry._BACKENDS, backend.id, backend)
    cfg = with_overrides(ReadioConfig(), engine=backend.id)
    plan = resolve_plan(cfg, _request(SynthesisRequest(engine=backend.id, language="en-us")))

    assert plan.ok, [diagnostic.message for diagnostic in plan.diagnostics]
    sink = _Sink()
    summary = render_from_plan(
        plan,
        InputDocument(text="Hello.", source_path=None, format="text"),
        sink,
    )

    assert summary.sample_rate == 24000
    assert summary.sample_count > 0
    assert sink.chunks
