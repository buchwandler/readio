"""Shared, benchmark-local utilities for Readio's optional Redux benchmarks.

This module intentionally depends on Readio only through its public API. Heavy
ASR dependencies are imported lazily so normal test and CLI discovery paths do
not require a model installation.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from readio.verification.cases import READIO_E2E_CASE
from readio.verification.text import EditDistance as ErrorRate
from readio.verification.text import TextVerificationResult as VerificationResult
from readio.verification.text import (
    VerificationThresholds,
    character_error_rate,
    edit_distance,
    normalize_text,
    word_error_rate,
)
from readio.verification.text import classify_verification as _classify_verification
from readio.verification.text import verify_text as _verify_text

DEFAULT_TEXT = READIO_E2E_CASE.text
DEFAULT_REDUX_MODEL = "moondream/parakeet-redux"
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_PASS_WER = 0.10
DEFAULT_PASS_CER = 0.05
DEFAULT_FAIL_WER = 0.20
DEFAULT_FAIL_CER = 0.10
RESULT_SCHEMA = "readio.benchmark.redux.v1"
VOICE_MATRIX_SCHEMA = "readio.benchmark.redux.voice-matrix.v1"


BenchmarkStatus = Literal["pass", "review", "fail"]


class BenchmarkError(RuntimeError):
    """A benchmark preflight or output invariant failed."""


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    text: str
    expected_text: str | None = None

    @property
    def reference_text(self) -> str:
        return self.expected_text if self.expected_text is not None else self.text


def default_case() -> BenchmarkCase:
    return BenchmarkCase(
        name="redux-e2e-fixed-english",
        text=READIO_E2E_CASE.text,
        expected_text=READIO_E2E_CASE.expected_text,
    )


def parse_engine_options(values: Sequence[str] | None) -> dict[str, Any]:
    """Parse repeatable KEY=VALUE options using conservative scalar coercion."""
    options: dict[str, Any] = {}
    for item in values or ():
        key, separator, raw_value = item.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"engine option must be KEY=VALUE, got {item!r}")
        key = key.strip()
        value: Any = raw_value
        lowered = raw_value.casefold()
        if lowered == "true":
            value = True
        elif lowered == "false":
            value = False
        elif lowered == "null":
            value = None
        else:
            try:
                number = json.loads(raw_value)
            except (json.JSONDecodeError, TypeError):
                pass
            else:
                if isinstance(number, (int, float)) and not isinstance(number, bool):
                    value = number
        options[key] = value
    return options


def _safe_engine_options(options: Mapping[str, Any]) -> dict[str, Any]:
    """Redact option values whose names indicate credentials before JSON output."""
    secret_markers = (
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "credential",
        "auth",
        "access_key",
        "private_key",
        "bearer",
    )
    return {
        key: "<redacted>" if any(marker in key.casefold() for marker in secret_markers) else value
        for key, value in options.items()
    }


@dataclass(frozen=True)
class AudioInspection:
    frames: int
    sample_rate: int
    channels: int
    duration_seconds: float


@dataclass(frozen=True)
class BenchmarkResult:
    schema: str = RESULT_SCHEMA
    status: BenchmarkStatus = "fail"
    case: str = ""
    language: str = ""
    engine: str = ""
    model: str = ""
    engine_options: Mapping[str, Any] = field(default_factory=dict)
    voice: str = ""
    voice_ref: str | None = None
    resolved_language: str | None = None
    resolved_engine: str | None = None
    resolved_model: str | None = None
    resolved_voice: str | None = None
    project_id: str | None = None
    project_path: str | None = None
    plan_ids: tuple[str, ...] = ()
    scope_count: int | None = None
    planned_units: int | None = None
    synthesis_profile_id: str | None = None
    selected_units: int | None = None
    rendered_units: int | None = None
    reused_units: int | None = None
    active_synthesis: bool | None = None
    composition_id: str | None = None
    composition_items: int | None = None
    loudness: Mapping[str, Any] | None = None
    wav: str | None = None
    wav_sha256: str | None = None
    input_sha256: str | None = None
    audio_seconds: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    project_seconds: float | None = None
    resolution_seconds: float | None = None
    plan_seconds: float | None = None
    synthesis_seconds: float | None = None
    composition_seconds: float | None = None
    redux_load_seconds: float | None = None
    redux_seconds: float | None = None
    total_seconds: float = 0.0
    synthesis_x_real_time: float | None = None
    redux_x_real_time: float | None = None
    redux_model: str = DEFAULT_REDUX_MODEL
    redux_revision: str | None = None
    redux_device: str = "cpu"
    verification: VerificationResult | None = None
    environment: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    failure_stage: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def write_json(self, path: Path) -> None:
        write_json(path, self)


def classify_verification(
    *,
    wer: float,
    cer: float,
    transcript: str,
    pass_wer: float = DEFAULT_PASS_WER,
    pass_cer: float = DEFAULT_PASS_CER,
    fail_wer: float = DEFAULT_FAIL_WER,
    fail_cer: float = DEFAULT_FAIL_CER,
) -> BenchmarkStatus:
    return _classify_verification(
        wer=wer,
        cer=cer,
        transcript=transcript,
        thresholds=VerificationThresholds(pass_wer, pass_cer, fail_wer, fail_cer),
    )


def verify(
    *,
    expected: str,
    transcript: str,
    pass_wer: float = DEFAULT_PASS_WER,
    pass_cer: float = DEFAULT_PASS_CER,
    fail_wer: float = DEFAULT_FAIL_WER,
    fail_cer: float = DEFAULT_FAIL_CER,
) -> VerificationResult:
    return _verify_text(
        expected=expected,
        transcript=transcript,
        thresholds=VerificationThresholds(pass_wer, pass_cer, fail_wer, fail_cer),
    )


_VERIFICATION_COMPATIBILITY_EXPORTS = (
    ErrorRate,
    VerificationResult,
    VerificationThresholds,
    character_error_rate,
    edit_distance,
    normalize_text,
    word_error_rate,
    verify,
    classify_verification,
)


def inspect_wav(
    path: Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = 1,
) -> AudioInspection:
    """Validate the Redux input audio contract without modifying the WAV."""
    if not path.is_file():
        raise BenchmarkError(f"master WAV does not exist: {path}")
    try:
        import soundfile as sf

        info = sf.info(str(path))
    except Exception as error:
        raise BenchmarkError(f"cannot inspect master WAV {path}: {error}") from error

    duration = float(info.duration)
    if info.frames <= 0 or duration <= 0:
        raise BenchmarkError(f"master WAV contains no audio frames: {path}")
    if info.samplerate != sample_rate:
        raise BenchmarkError(
            f"master WAV sample rate is {info.samplerate}, expected {sample_rate} Hz"
        )
    if info.channels != channels:
        raise BenchmarkError(f"master WAV has {info.channels} channels, expected {channels}")
    return AudioInspection(
        frames=int(info.frames),
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        duration_seconds=duration,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _package_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
        return None


def environment_metadata(
    *, engine: str, redux_model: str = DEFAULT_REDUX_MODEL, redux_device: str = "cpu"
) -> dict[str, Any]:
    engine_packages = {
        "kokoro": "pykokoro",
        "piper": "pipersynth",
        "pocket": "pocketsynth",
        "kitten": "kittensynth",
        "supertonic": "supertonicsynth",
    }
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "readio_version": _package_version("readio"),
        "engine_package": engine_packages.get(engine, engine),
        "engine_package_version": _package_version(engine_packages.get(engine, engine)),
        "moondream_version": _package_version("moondream"),
        "redux_model": redux_model,
        "redux_revision": None,
        "redux_device": redux_device,
        "cpu_count": os.cpu_count(),
    }


def to_jsonable(value: Any) -> Any:
    """Convert benchmark records and paths to stable standard JSON values."""
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"cannot serialize benchmark value of type {type(value).__name__}")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            to_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n",
        encoding="utf-8",
    )


def run_default_e2e(work_dir: Path, **options: Any) -> BenchmarkResult:
    """Invoke the single-case CLI runner for opt-in integration tests."""
    from .benchmark_e2e import run_default_e2e as execute_default_e2e

    return execute_default_e2e(work_dir, **options)
