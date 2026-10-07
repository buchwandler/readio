from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import numpy as np
import soundfile as sf

from readio.api import (
    DiscoveryOptions,
    Readio,
    ReadioEvent,
    SelfTestRequest,
    SynthesisRequest,
    VoiceInfo,
    VoiceMatrixRequest,
)
from readio.errors import ProjectPlanRenderabilityError
from readio.verification.cases import DEFAULT_TEXT
from readio.verification.types import TranscriptionResult


class _Redux:
    model = "mock/redux"
    device = "cpu"
    load_seconds = 0.2

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def transcribe(self, _audio: Path, *, timestamps: str) -> TranscriptionResult:
        assert timestamps == "word"
        return TranscriptionResult(text=DEFAULT_TEXT)


class _Projects:
    def __init__(self, tmp_path: Path, *, blocked: bool = False) -> None:
        self.tmp_path = tmp_path
        self.blocked = blocked
        self.calls: list[str] = []

    def create(self, source: Path, *, output: Path) -> SimpleNamespace:
        self.calls.append("create")
        assert source.read_text(encoding="utf-8")
        return SimpleNamespace(root=output, project_id="project")

    def resolve_synthesis(self, _project: Any, request: Any, *, use_saved_settings: bool):
        self.calls.append("resolve")
        assert use_saved_settings is False
        return SimpleNamespace(
            language=request.language,
            engine=request.engine,
            model=request.model,
            voice="af_sarah",
        )

    def plan(self, _project: Any, *, options: Any):
        self.calls.append("plan")
        assert options.renderability == "repair"
        if self.blocked:
            raise ProjectPlanRenderabilityError(
                ({"scope_id": "document", "reason": "too_long"},),
                renderability_mode="repair",
                attempt_id="attempt-blocked",
                attempt_status="blocked",
            )
        return SimpleNamespace(
            scopes=(SimpleNamespace(units=2, plan_id="plan"),),
            renderability_mode="repair",
            renderability_guaranteed=True,
            attempt_id="attempt-1",
            activated=True,
            repairs=1,
            reused_scopes=(),
            rebuilt_scopes=("document",),
            diagnostics=(),
        )

    def inspect_plan(self, _project: Any, *, options: Any):
        self.calls.append("inspect")
        assert options.attempt == "latest"
        issue = SimpleNamespace(
            scope_id="document",
            code="segment.too_long",
            reason="too_long",
            segment_id="segment-1",
            unit_id=None,
            line=1,
            repair_safe=True,
            text="broken",
        )
        return SimpleNamespace(
            attempt=SimpleNamespace(attempt_id="attempt-blocked", status="blocked"),
            issues=(issue,),
            repairs=(issue,),
        )

    def synthesize(self, _project: Any, _request: Any, *, activate: bool):
        self.calls.append("synthesize")
        assert activate
        return SimpleNamespace(
            profile_id="profile-1", selected_units=2, rendered=2, reused=0, activated=True
        )

    def compose(self, _project: Any, options: Any):
        self.calls.append("compose")
        assert options.sample_rate == 16_000
        wav = self.tmp_path / "master.wav"
        sf.write(wav, np.ones(320, dtype=np.float32) * 0.1, 16_000)
        return SimpleNamespace(
            composition_id="composition-1", master_path=wav, frames=320, items=1, loudness=None
        )


def test_e2e_records_planning_provenance_and_composed_master(tmp_path: Path, monkeypatch) -> None:
    import readio.integrations.moondream as integration

    monkeypatch.setattr(integration, "ReduxSession", _Redux)
    projects = _Projects(tmp_path)
    events: list[ReadioEvent] = []
    app = Readio(on_event=events.append)
    app._services["ProjectService"] = projects
    request = SelfTestRequest(
        synthesis=SynthesisRequest(
            language="en-us", engine="kokoro", model="v1.0", voice="kokoro:v1.0/af_sarah"
        ),
        output=tmp_path / "e2e",
        require_clean_plan=True,
    )
    result = app.verification.run_e2e(request)
    assert result.overall_status == "review"
    assert result.planning["attempt_id"] == "attempt-1"
    assert result.planning["repairs"] == 1
    assert result.synthesis["rendered_units"] == 2
    assert result.composition["wav_sha256"]
    assert result.verification["wer"] == 0.0
    assert projects.calls == ["create", "resolve", "plan", "synthesize", "compose"]
    payload = json.loads((tmp_path / "e2e" / "result.json").read_text(encoding="utf-8"))
    assert payload["schema"] == "readio.verification.e2e.v1"
    assert payload["planning"]["renderability_mode"] == "repair"

    assert payload == result.to_dict()
    assert events[0].kind == "operation.started"
    assert events[0].operation == "verification.e2e"
    assert events[-1].kind == "operation.completed"
    assert events[-1].details["status"] == result.overall_status
    assert any(
        event.kind == "stage.completed" and event.stage == "verification" for event in events
    )


def test_e2e_applies_request_specific_wer_cer_thresholds(tmp_path: Path, monkeypatch) -> None:
    import readio.integrations.moondream as integration

    class NoisyRedux(_Redux):
        def transcribe(self, _audio: Path, *, timestamps: str) -> TranscriptionResult:
            return TranscriptionResult(text=DEFAULT_TEXT.replace("Clear", "Clearly", 1))

    monkeypatch.setattr(integration, "ReduxSession", NoisyRedux)
    app = Readio()
    app._services["ProjectService"] = _Projects(tmp_path)
    request = SelfTestRequest(
        synthesis=SynthesisRequest(
            language="en-us", engine="kokoro", model="v1.0", voice="af_sarah"
        ),
        output=tmp_path / "custom-thresholds",
        pass_wer=0.0,
        pass_cer=0.0,
        fail_wer=0.5,
        fail_cer=0.5,
    )

    result = app.verification.run_e2e(request)

    assert result.overall_status == "review"
    assert result.verification["wer"] > 0
    assert result.requested["thresholds"] == {
        "pass_wer": 0.0,
        "pass_cer": 0.0,
        "fail_wer": 0.5,
        "fail_cer": 0.5,
    }


def test_blocked_plan_is_inspected_and_stops_before_synthesis(tmp_path: Path, monkeypatch) -> None:
    import readio.integrations.moondream as integration

    monkeypatch.setattr(integration, "ReduxSession", _Redux)
    projects = _Projects(tmp_path, blocked=True)
    app = Readio()
    app._services["ProjectService"] = projects
    result = app.verification.run_e2e(
        SelfTestRequest(
            synthesis=SynthesisRequest(
                language="en-us", engine="kokoro", model="v1.0", voice="af_sarah"
            ),
            output=tmp_path / "blocked",
        )
    )
    assert result.overall_status == "fail"
    assert result.failure_stage == "planning"
    attempt = result.attempts[0]
    assert attempt["failure_stage"] == "planning"
    assert attempt["planning"]["attempt_id"] == "attempt-blocked"
    assert attempt["planning"]["repairs_available"] == 1
    assert projects.calls == ["create", "resolve", "plan", "inspect"]
    assert (tmp_path / "blocked" / "attempt-001" / "source.txt").is_file()
    assert not (tmp_path / "blocked" / "attempt-001" / "project.readio" / "synthesis").exists()


def test_pocket_short_tail_repetitions_keep_fresh_projects_and_terminal_completeness(
    tmp_path: Path, monkeypatch
) -> None:
    import readio.integrations.moondream as integration

    instances = []

    class PocketRedux(_Redux):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            instances.append(self)

        def transcribe(self, _audio: Path, *, timestamps: str) -> TranscriptionResult:
            return TranscriptionResult(text="Hello, how are you?")

    monkeypatch.setattr(integration, "ReduxSession", PocketRedux)
    projects = _Projects(tmp_path)
    app = Readio()
    app._services["ProjectService"] = projects
    result = app.verification.run_e2e(
        SelfTestRequest(
            case="pocket-short-tail",
            repetitions=3,
            synthesis=SynthesisRequest(
                language="en",
                engine="pocket",
                model="english_2026-04",
                voice="pocket:english_2026-04/alba",
            ),
            output=tmp_path / "pocket",
        )
    )
    assert len(instances) == 1
    assert result.overall_status == "pass"
    assert [attempt["verification"]["terminal_complete"] for attempt in result.attempts] == [
        True
    ] * 3
    assert len({attempt["project_path"] for attempt in result.attempts}) == 3
    summary = result.verification["short_tail"]
    assert summary["runs"] == 3
    assert summary["pass_count"] == 3
    assert summary["fail_count"] == 0


def test_voice_matrix_filters_catalog_shares_redux_and_writes_provenance(
    tmp_path: Path, monkeypatch
) -> None:
    import readio.integrations.moondream as integration

    instances = []
    queried = []

    class CountingRedux(_Redux):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            instances.append(self)

    def voice(ref: str, *, experimental: bool = False, runtime_available: bool = True) -> VoiceInfo:
        voice_id = ref.rsplit("/", 1)[-1]
        return VoiceInfo(
            ref=ref,
            id=voice_id,
            gender="female",
            language="en-us",
            locale="en-US",
            language_label="English",
            model="v1.0",
            source="fixture",
            default=False,
            status="ready",
            experimental=experimental,
            runtime_available=runtime_available,
            engine="kokoro",
        )

    entries = (
        voice("kokoro:v1.0/af_sarah"),
        voice("kokoro:v1.0/af_nicole", experimental=True),
        voice("kokoro:v1.0/unavailable", runtime_available=False),
    )
    catalog = SimpleNamespace(
        voices_listing=lambda query, *, discovery: (
            queried.append((query, discovery))
            or SimpleNamespace(items=entries, discovery=SimpleNamespace(registry_source="fixture"))
        )
    )
    monkeypatch.setattr(integration, "ReduxSession", CountingRedux)
    app = Readio()
    app._services["ProjectService"] = _Projects(tmp_path)
    app._services["CatalogService"] = catalog
    output = tmp_path / "matrix"
    result = app.verification.generate_voices(
        VoiceMatrixRequest(
            synthesis=SynthesisRequest(
                language="en-us",
                engine="kokoro",
                model="v1.0",
                engine_options={"api_key": "secret"},
            ),
            include_experimental=True,
            discovery=DiscoveryOptions(offline=True),
            output=output,
        )
    )
    assert len(instances) == 1
    assert len(result.results) == 2
    assert result.overall_status == "pass"
    assert result.summary["passed"] == 2
    assert result.results[0]["planning_status"] == "repaired"
    assert result.results[0]["synthesis_status"] == "pass"
    assert len({row["project_path"] for row in result.results}) == 2
    assert result.requested["engine_options"]["api_key"] == "<redacted>"
    assert "secret" not in (output / "results.json").read_text(encoding="utf-8")
    assert queried[0][0].engine == "kokoro"
    assert queried[0][1].offline is True
    assert (output / "results.json").is_file()
    csv_text = (output / "results.csv").read_text(encoding="utf-8")
    assert "voice_ref" in csv_text.splitlines()[0]
    assert "transcript" not in csv_text.splitlines()[0]
