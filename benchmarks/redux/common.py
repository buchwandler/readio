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
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from types import TracebackType
from typing import Any, Literal, Protocol

from typing_extensions import Self

DEFAULT_REDUX_MODEL = "moondream/parakeet-redux"
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_PASS_WER = 0.10
DEFAULT_PASS_CER = 0.05
DEFAULT_FAIL_WER = 0.20
DEFAULT_FAIL_CER = 0.10
RESULT_SCHEMA = "readio.benchmark.redux.v1"
VOICE_MATRIX_SCHEMA = "readio.benchmark.redux.voice-matrix.v1"

DEFAULT_TEXT = (
    "Clear speech makes long listening easier. This benchmark checks planning,\n"
    "synthesis, composition, and transcription from beginning to end. The final\n"
    "recording should contain every sentence in the same order, with no missing\n"
    "words, repeated phrases, or unexpected speech."
)

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
    return BenchmarkCase(name="redux-e2e-fixed-english", text=DEFAULT_TEXT)


@dataclass(frozen=True)
class TranscriptWord:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Transcript:
    text: str
    words: tuple[TranscriptWord, ...] = ()


class Transcriber(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def device(self) -> str: ...

    @property
    def load_seconds(self) -> float | None: ...

    def transcribe(self, wav: Path) -> Transcript: ...


@dataclass(frozen=True)
class ErrorRate:
    distance: int
    substitutions: int
    deletions: int
    insertions: int
    rate: float


@dataclass(frozen=True)
class VerificationResult:
    expected: str
    transcript: str
    normalized_expected: str
    normalized_transcript: str
    wer: float
    cer: float
    wer_distance: int
    wer_substitutions: int
    wer_deletions: int
    wer_insertions: int
    cer_distance: int
    status: BenchmarkStatus


@dataclass(frozen=True)
class AudioInspection:
    frames: int
    sample_rate: int
    channels: int
    duration_seconds: float


@dataclass(frozen=True)
class StageTiming:
    seconds: float


@dataclass(frozen=True)
class BenchmarkResult:
    schema: str = RESULT_SCHEMA
    status: BenchmarkStatus = "fail"
    case: str = ""
    language: str = ""
    engine: str = ""
    model: str = ""
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


_APOSTROPHES = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "‛": "'",
        "ʼ": "'",
        "＇": "'",
        "`": "'",
        "´": "'",
    }
)


def normalize_text(value: str) -> str:
    """Normalize formatting while preserving the spoken words and apostrophes."""
    value = unicodedata.normalize("NFKC", value).casefold().translate(_APOSTROPHES)
    value = re.sub(r"[^\w\s']", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def edit_distance(reference: Sequence[Any], hypothesis: Sequence[Any]) -> ErrorRate:
    """Return Levenshtein distance and a deterministic operation breakdown."""
    ref_len, hyp_len = len(reference), len(hypothesis)
    distances = [[0] * (hyp_len + 1) for _ in range(ref_len + 1)]
    for row in range(1, ref_len + 1):
        distances[row][0] = row
    for column in range(1, hyp_len + 1):
        distances[0][column] = column

    for row in range(1, ref_len + 1):
        for column in range(1, hyp_len + 1):
            substitution_cost = 0 if reference[row - 1] == hypothesis[column - 1] else 1
            distances[row][column] = min(
                distances[row - 1][column] + 1,
                distances[row][column - 1] + 1,
                distances[row - 1][column - 1] + substitution_cost,
            )

    row, column = ref_len, hyp_len
    substitutions = deletions = insertions = 0
    while row or column:
        if (
            row
            and column
            and reference[row - 1] == hypothesis[column - 1]
            and distances[row][column] == distances[row - 1][column - 1]
        ):
            row -= 1
            column -= 1
        elif row and column and distances[row][column] == distances[row - 1][column - 1] + 1:
            substitutions += 1
            row -= 1
            column -= 1
        elif row and distances[row][column] == distances[row - 1][column] + 1:
            deletions += 1
            row -= 1
        else:
            insertions += 1
            column -= 1

    distance = distances[ref_len][hyp_len]
    denominator = ref_len
    rate = distance / denominator if denominator else (0.0 if not hyp_len else 1.0)
    return ErrorRate(distance, substitutions, deletions, insertions, rate)


def word_error_rate(reference: str, hypothesis: str) -> float:
    return edit_distance(normalize_text(reference).split(), normalize_text(hypothesis).split()).rate


def character_error_rate(reference: str, hypothesis: str) -> float:
    ref = normalize_text(reference).replace(" ", "")
    hyp = normalize_text(hypothesis).replace(" ", "")
    return edit_distance(ref, hyp).rate


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
    if not normalize_text(transcript):
        return "fail"
    if wer <= pass_wer and cer <= pass_cer:
        return "pass"
    if wer <= fail_wer and cer <= fail_cer:
        return "review"
    return "fail"


def verify(
    *,
    expected: str,
    transcript: str,
    pass_wer: float = DEFAULT_PASS_WER,
    pass_cer: float = DEFAULT_PASS_CER,
    fail_wer: float = DEFAULT_FAIL_WER,
    fail_cer: float = DEFAULT_FAIL_CER,
) -> VerificationResult:
    normalized_expected = normalize_text(expected)
    normalized_transcript = normalize_text(transcript)
    wer_stats = edit_distance(normalized_expected.split(), normalized_transcript.split())
    cer_stats = edit_distance(
        normalized_expected.replace(" ", ""), normalized_transcript.replace(" ", "")
    )
    return VerificationResult(
        expected=expected,
        transcript=transcript,
        normalized_expected=normalized_expected,
        normalized_transcript=normalized_transcript,
        wer=wer_stats.rate,
        cer=cer_stats.rate,
        wer_distance=wer_stats.distance,
        wer_substitutions=wer_stats.substitutions,
        wer_deletions=wer_stats.deletions,
        wer_insertions=wer_stats.insertions,
        cer_distance=cer_stats.distance,
        status=classify_verification(
            wer=wer_stats.rate,
            cer=cer_stats.rate,
            transcript=transcript,
            pass_wer=pass_wer,
            pass_cer=pass_cer,
            fail_wer=fail_wer,
            fail_cer=fail_cer,
        ),
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


def _field(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def filter_voices(voices: Sequence[Any], *, include_experimental: bool = False) -> tuple[Any, ...]:
    """Keep catalog voices that can run, preserving their catalog order."""
    return tuple(
        voice
        for voice in voices
        if bool(_field(voice, "runtime_available", False))
        and (include_experimental or not bool(_field(voice, "experimental", False)))
    )


def safe_name(value: str) -> str:
    """Return a filesystem-safe, stable lowercase component."""
    return re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-") or "voice"


def voice_wav_name(ordinal: int, engine: str, model: str, voice: str) -> str:
    if ordinal < 1:
        raise ValueError("voice ordinal must be a positive integer")
    return f"{ordinal:03d}-{safe_name(engine)}-{safe_name(model)}-{safe_name(voice)}.wav"


def numeric_summary(values: Sequence[float | int | None]) -> dict[str, float | int] | None:
    samples = sorted(float(value) for value in values if value is not None and math.isfinite(value))
    if not samples:
        return None
    result: dict[str, float | int] = {
        "mean": sum(samples) / len(samples),
        "median": (samples[(len(samples) - 1) // 2] + samples[len(samples) // 2]) / 2,
        "min": samples[0],
        "max": samples[-1],
    }
    if len(samples) >= 20:
        result["p95"] = samples[math.ceil(0.95 * len(samples)) - 1]
    return result


def aggregate_results(results: Sequence[Any]) -> dict[str, Any]:
    """Create stable, transcript-free voice-matrix counts and statistics."""
    status_counts = {status: 0 for status in ("pass", "review", "fail")}
    for result in results:
        status = str(_field(result, "status", "fail")).casefold()
        if status in status_counts:
            status_counts[status] += 1

    def metric_summary(
        attribute: str, *, verification: bool = False
    ) -> dict[str, float | int] | None:
        values: list[float | None] = []
        for result in results:
            source = _field(result, "verification") if verification else result
            value = _field(source, attribute) if source is not None else None
            values.append(float(value) if isinstance(value, (int, float)) else None)
        return numeric_summary(values)

    def extreme(attribute: str, *, verification: bool = False) -> dict[str, Any] | None:
        candidates: list[tuple[float, Any]] = []
        for result in results:
            source = _field(result, "verification") if verification else result
            value = _field(source, attribute) if source is not None else None
            if isinstance(value, (int, float)) and math.isfinite(value):
                candidates.append((float(value), result))
        if not candidates:
            return None
        value, result = max(candidates, key=lambda item: item[0])
        return {"voice": _field(result, "voice"), attribute: value}

    return {
        "voice_count": len(results),
        "passed": status_counts["pass"],
        "review": status_counts["review"],
        "failed": status_counts["fail"],
        "status_counts": status_counts,
        "wer": metric_summary("wer", verification=True),
        "cer": metric_summary("cer", verification=True),
        "synthesis_seconds": metric_summary("synthesis_seconds"),
        "composition_seconds": metric_summary("composition_seconds"),
        "redux_seconds": metric_summary("redux_seconds"),
        "synthesis_x_real_time": metric_summary("synthesis_x_real_time"),
        "redux_x_real_time": metric_summary("redux_x_real_time"),
        "slowest_synthesis": extreme("synthesis_seconds"),
        "highest_wer": extreme("wer", verification=True),
    }


class ReduxTranscriber:
    """Thin lazy adapter around Moondream Photon / Parakeet Redux."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_REDUX_MODEL,
        device: str = "cpu",
    ) -> None:
        self.model = model
        self.device = device
        self.load_seconds: float | None = None
        self._manager: Any = None
        self._speech: Any = None

    def __enter__(self) -> Self:
        import moondream as md

        started = perf_counter()
        photon = getattr(md, "photon", None)
        if not callable(photon):
            raise BenchmarkError("installed moondream package does not expose photon()")
        self._manager = photon(self.model, device=self.device)
        try:
            self._speech = self._manager.__enter__()
        except BaseException:
            self._manager = None
            raise
        self.load_seconds = perf_counter() - started
        return self

    def transcribe(self, wav: Path) -> Transcript:
        if self._speech is None:
            raise RuntimeError("ReduxTranscriber must be entered before transcription")
        raw = self._speech.transcribe(audio=wav, timestamps="word")
        text = str(_field(raw, "text", "") or "")
        raw_words = _field(raw, "words", None)
        if raw_words is None:
            raw_words = _field(raw, "timestamps", ())
        words: list[TranscriptWord] = []
        if isinstance(raw_words, Sequence) and not isinstance(raw_words, (str, bytes)):
            for item in raw_words:
                word_text = _field(item, "text", _field(item, "word", ""))
                if not word_text:
                    continue
                try:
                    start = float(_field(item, "start", 0.0) or 0.0)
                    end = float(_field(item, "end", start) or 0.0)
                except (TypeError, ValueError):
                    start = end = 0.0
                words.append(TranscriptWord(str(word_text), start, end))
        if not text and words:
            text = " ".join(word.text for word in words)
        return Transcript(text=text, words=tuple(words))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        manager, self._manager, self._speech = self._manager, None, None
        if manager is not None:
            return manager.__exit__(exc_type, exc, traceback)
        return None


def run_case(
    *,
    app: Any,
    case: BenchmarkCase,
    language: str,
    engine: str,
    model: str,
    voice: str,
    project_dir: Path,
    redux: Transcriber,
    source_path: Path | None = None,
    engine_device: str | None = None,
    pass_wer: float = DEFAULT_PASS_WER,
    pass_cer: float = DEFAULT_PASS_CER,
    fail_wer: float = DEFAULT_FAIL_WER,
    fail_cer: float = DEFAULT_FAIL_CER,
) -> BenchmarkResult:
    """Run one complete project -> plan -> synthesis -> composition -> Redux case.

    Errors are returned as failed results instead of escaping so callers can
    persist diagnostics and, for a voice matrix, continue with the next voice.
    """
    started = perf_counter()
    timings: dict[str, float] = {}
    failure_stage = "configuration"
    values: dict[str, Any] = {
        "case": case.name,
        "language": language,
        "engine": engine,
        "model": model,
        "voice": voice,
        "redux_model": getattr(redux, "model", DEFAULT_REDUX_MODEL),
        "redux_device": getattr(redux, "device", "cpu"),
        "environment": environment_metadata(
            engine=engine,
            redux_model=getattr(redux, "model", DEFAULT_REDUX_MODEL),
            redux_device=getattr(redux, "device", "cpu"),
        ),
    }

    def timed(name: str, action: Any) -> Any:
        stage_started = perf_counter()
        try:
            return action()
        finally:
            timings[name] = max(0.0, perf_counter() - stage_started)

    try:
        from readio.api import CompositionOptions, SynthesisRequest

        project_dir = Path(project_dir)
        source = (
            Path(source_path)
            if source_path is not None
            else (project_dir.parent / f"{project_dir.name}.txt")
        )
        source.parent.mkdir(parents=True, exist_ok=True)
        failure_stage = "source_creation"
        source.write_text(case.text, encoding="utf-8")
        values["input_sha256"] = sha256_file(source)

        failure_stage = "project_creation"
        project = timed("project_seconds", lambda: app.projects.create(source, output=project_dir))
        values["project_id"] = _field(project, "project_id")
        values["project_path"] = str(_field(project, "root", project_dir))

        failure_stage = "preflight"
        resolution_started = perf_counter()
        try:
            voice_resolution = app.catalog.resolve_voice(voice, engine=engine)
            voice_entry = _field(voice_resolution, "catalog_entry")
            resolved_ref = _field(voice_resolution, "ref") or _field(voice_entry, "ref") or voice
            resolved_voice = _field(voice_resolution, "voice") or _field(voice_entry, "id")
            engine_options = {"device": engine_device} if engine_device is not None else {}
            request = SynthesisRequest(
                language=language,
                engine=engine,
                model=model,
                voice=voice,
                engine_options=engine_options,
            )
            resolution = app.projects.resolve_synthesis(project, request, use_saved_settings=False)
        finally:
            timings["resolution_seconds"] = max(0.0, perf_counter() - resolution_started)
        resolved_engine = _field(resolution, "engine")
        resolved_model = _field(resolution, "model")
        resolved_language = _field(resolution, "language")
        actual_voice = _field(resolution, "voice")
        values.update(
            {
                "voice_ref": resolved_ref,
                "resolved_language": resolved_language,
                "resolved_engine": resolved_engine,
                "resolved_model": resolved_model,
                "resolved_voice": actual_voice,
                "voice": actual_voice,
            }
        )
        for label, actual, expected in (
            ("engine", resolved_engine, engine),
            ("model", resolved_model, model),
            ("language", resolved_language, language),
            ("voice", actual_voice, resolved_voice),
        ):
            if (
                actual is None
                or expected is None
                or str(actual).casefold() != str(expected).casefold()
            ):
                raise BenchmarkError(
                    f"resolved {label} {actual!r} does not match requested target {expected!r}"
                )

        failure_stage = "planning"
        planned = timed("plan_seconds", lambda: app.projects.plan(project))
        scopes = tuple(_field(planned, "scopes", ()) or ())
        plan_ids = tuple(str(_field(scope, "plan_id", "") or "") for scope in scopes)
        planned_units = sum(int(_field(scope, "units", 0) or 0) for scope in scopes)
        if not scopes:
            raise BenchmarkError("planning produced no scopes")
        if any(not plan_id for plan_id in plan_ids):
            raise BenchmarkError("planning produced a scope without a plan id")
        if planned_units <= 0:
            raise BenchmarkError("planning produced no units")
        values.update(
            {"plan_ids": plan_ids, "scope_count": len(scopes), "planned_units": planned_units}
        )

        failure_stage = "synthesis"
        synthesized = timed(
            "synthesis_seconds",
            lambda: app.projects.synthesize(project, request, activate=True),
        )
        selected_units = int(_field(synthesized, "selected_units", 0) or 0)
        rendered_units = int(_field(synthesized, "rendered", 0) or 0)
        reused_units = int(_field(synthesized, "reused", 0) or 0)
        active_synthesis = bool(_field(synthesized, "activated", False))
        values.update(
            {
                "synthesis_profile_id": _field(synthesized, "profile_id"),
                "selected_units": selected_units,
                "rendered_units": rendered_units,
                "reused_units": reused_units,
                "active_synthesis": active_synthesis,
            }
        )
        if selected_units <= 0:
            raise BenchmarkError("synthesis selected no units")
        if rendered_units + reused_units != selected_units:
            raise BenchmarkError(
                "synthesis accounting mismatch: rendered + reused does not equal selected units"
            )
        if not active_synthesis:
            raise BenchmarkError("synthesis did not activate the selected profile")

        failure_stage = "composition"
        composed = timed(
            "composition_seconds",
            lambda: app.projects.compose(
                project, CompositionOptions(sample_rate=DEFAULT_SAMPLE_RATE)
            ),
        )
        master = _field(composed, "master_path")
        frames = int(_field(composed, "frames", 0) or 0)
        items = int(_field(composed, "items", 0) or 0)
        values.update(
            {
                "composition_id": _field(composed, "composition_id"),
                "composition_items": items,
                "loudness": to_jsonable(_field(composed, "loudness"))
                if _field(composed, "loudness") is not None
                else None,
            }
        )
        if master is None:
            raise BenchmarkError("composition did not produce a master WAV path")
        master = Path(master)
        values["wav"] = str(master)
        if not master.is_file():
            raise BenchmarkError(f"composition master WAV does not exist: {master}")
        if frames <= 0:
            raise BenchmarkError("composition produced no frames")
        if items <= 0:
            raise BenchmarkError("composition produced no items")
        failure_stage = "audio_validation"
        audio = inspect_wav(master)
        values.update(
            {
                "wav_sha256": sha256_file(master),
                "audio_seconds": audio.duration_seconds,
                "sample_rate": audio.sample_rate,
                "channels": audio.channels,
            }
        )

        failure_stage = "redux_transcription"
        transcript_result = timed("redux_seconds", lambda: redux.transcribe(master))
        if isinstance(transcript_result, str):
            transcript = transcript_result
        else:
            transcript = str(_field(transcript_result, "text", "") or "")
        verification = verify(
            expected=case.reference_text,
            transcript=transcript,
            pass_wer=pass_wer,
            pass_cer=pass_cer,
            fail_wer=fail_wer,
            fail_cer=fail_cer,
        )
        values["verification"] = verification
        values["redux_load_seconds"] = getattr(redux, "load_seconds", None)
        values["synthesis_x_real_time"] = (
            audio.duration_seconds / timings["synthesis_seconds"]
            if timings.get("synthesis_seconds", 0.0) > 0
            else None
        )
        values["redux_x_real_time"] = (
            audio.duration_seconds / timings["redux_seconds"]
            if timings.get("redux_seconds", 0.0) > 0
            else None
        )
        return BenchmarkResult(
            status=verification.status,
            total_seconds=perf_counter() - started,
            project_seconds=timings.get("project_seconds"),
            resolution_seconds=timings.get("resolution_seconds"),
            plan_seconds=timings.get("plan_seconds"),
            synthesis_seconds=timings.get("synthesis_seconds"),
            composition_seconds=timings.get("composition_seconds"),
            redux_seconds=timings.get("redux_seconds"),
            **values,
        )
    except Exception as error:  # noqa: BLE001 - convert any pipeline exception to a durable failed result
        values.setdefault("redux_load_seconds", getattr(redux, "load_seconds", None))
        source = (
            Path(source_path)
            if source_path is not None
            else (Path(project_dir).parent / f"{Path(project_dir).name}.txt")
        )
        if source.is_file() and "input_sha256" not in values:
            values["input_sha256"] = sha256_file(source)
        return BenchmarkResult(
            status="fail",
            total_seconds=perf_counter() - started,
            project_seconds=timings.get("project_seconds"),
            resolution_seconds=timings.get("resolution_seconds"),
            plan_seconds=timings.get("plan_seconds"),
            synthesis_seconds=timings.get("synthesis_seconds"),
            composition_seconds=timings.get("composition_seconds"),
            redux_seconds=timings.get("redux_seconds"),
            error=f"{type(error).__name__}: {error}",
            failure_stage=failure_stage,
            **values,
        )


def run_default_e2e(work_dir: Path, **options: Any) -> BenchmarkResult:
    """Invoke the single-case CLI runner for opt-in integration tests."""
    from .benchmark_e2e import run_default_e2e as execute_default_e2e

    return execute_default_e2e(work_dir, **options)
