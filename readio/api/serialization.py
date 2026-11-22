"""Versioned JSON projections for Readio's public API contracts."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from readio.api.types import (
    ErrorInfo,
    ExecutionReport,
    ExportFormatCapability,
    ReadioCapabilities,
    RenderResult,
    SourceCapabilities,
    SpeakResult,
    StageExecutionRecord,
    SynthesisCapabilities,
    WorkflowPlan,
)


def to_json_value(value: Any) -> Any:
    """Convert ordinary nested values for diagnostic and legacy CLI output."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return to_json_value(vars(value))
    except TypeError:
        return str(value)


def _artifact_ref_to_dict(ref: Any) -> dict[str, object]:
    return {
        "kind": ref.kind,
        "owner": ref.owner,
        "path": to_json_value(ref.path),
        "identity": ref.identity,
        "state": ref.state,
    }


def _blocker_to_dict(blocker: Any) -> dict[str, object]:
    return {
        "code": blocker.code,
        "message": blocker.message,
        "owner": blocker.owner,
        "recoverable": blocker.recoverable,
        "suggested_action": blocker.suggested_action,
    }


def workflow_plan_to_dict(plan: WorkflowPlan) -> dict[str, object]:
    """Serialize a workflow plan using the readio.workflow-plan.v1 schema."""
    result: dict[str, object] = {
        "schema_version": plan.schema_version,
        "executable": plan.executable,
        "source": to_json_value(plan.source),
        "workspace": to_json_value(plan.workspace),
        "workspace_mode": plan.workspace_mode,
        "variant": plan.variant,
        "output": to_json_value(plan.output),
        "steps": [
            {
                "stage": step.stage,
                "package": step.package,
                "action": step.action,
                "selector": step.selector,
                "reason": step.reason,
                "reason_code": step.reason_code,
                "force": step.force,
                "state": step.state,
                "inputs": [_artifact_ref_to_dict(ref) for ref in step.inputs],
                "outputs": [_artifact_ref_to_dict(ref) for ref in step.outputs],
                "blockers": [_blocker_to_dict(blocker) for blocker in step.blockers],
                "details": to_json_value(step.details),
            }
            for step in plan.steps
        ],
        "warnings": list(plan.warnings),
    }
    return {"schema": "readio.workflow-plan.v1", "result": result}


def _stage_execution_record_to_dict(record: StageExecutionRecord) -> dict[str, object]:
    return {
        "stage": record.stage,
        "package": record.package,
        "action": record.action,
        "selector": record.selector,
        "state_before": record.state_before,
        "state_after": record.state_after,
        "elapsed_seconds": record.elapsed_seconds,
        "reason_code": record.reason_code,
    }


def _execution_report_to_dict(report: ExecutionReport) -> dict[str, object]:
    return {
        "source": to_json_value(report.source),
        "workspace": to_json_value(report.workspace),
        "workspace_mode": report.workspace_mode,
        "selected_chapters": report.selected_chapters,
        "profile": to_json_value(report.profile),
        "variant": report.variant,
        "output": to_json_value(report.output),
        "output_format": report.output_format,
        "stages": [_stage_execution_record_to_dict(stage) for stage in report.stages],
        "specialist_versions": to_json_value(report.specialist_versions),
        "warnings": list(report.warnings),
        "elapsed_seconds": report.elapsed_seconds,
    }


def render_result_to_dict(result: RenderResult) -> dict[str, object]:
    """Serialize a render result using the readio.render-result.v1 schema."""
    value: dict[str, object] = {
        "output": to_json_value(result.output),
        "workspace": to_json_value(result.workspace),
        "workspace_mode": result.workspace_mode,
        "variant": result.variant,
        "executed_stages": list(result.executed_stages),
        "reused_stages": list(result.reused_stages),
        "warnings": list(result.warnings),
    }
    if result.report is not None:
        value["report"] = _execution_report_to_dict(result.report)
    return {"schema": "readio.render-result.v1", "result": value}


def speak_result_to_dict(result: SpeakResult) -> dict[str, object]:
    """Serialize synthesized speech with an in-memory WAV payload."""
    audio = result.audio
    return {
        "schema": "readio.speak-result.v1",
        "result": {
            "profile_id": result.profile_id,
            "warnings": list(result.warnings),
            "elapsed_seconds": result.elapsed_seconds,
            "audio": {
                "media_type": "audio/wav",
                "encoding": "base64",
                "data": b64encode(audio.to_wav_bytes()).decode("ascii"),
                "sample_rate": audio.sample_rate,
                "channels": audio.channels,
                "frames": audio.frames,
                "duration_seconds": audio.duration_seconds,
            },
        },
    }


def error_info_to_dict(info: ErrorInfo) -> dict[str, object]:
    """Serialize a normalized error using the readio.error-info.v1 schema."""
    return {
        "schema": "readio.error-info.v1",
        "result": {
            "type": info.type,
            "message": info.message,
            "code": info.code,
            "stage": info.stage,
            "package": info.package,
            "selector": info.selector,
            "details": to_json_value(info.details),
            "recoverable": info.recoverable,
            "suggested_action": info.suggested_action,
        },
    }


def _engine_info_to_dict(engine: Any) -> dict[str, object]:
    return {
        "id": engine.id,
        "version": engine.version,
        "installed": engine.installed,
        "runnable": engine.runnable,
        "features": list(engine.features),
        "reason": engine.reason,
    }


def _export_format_to_dict(item: ExportFormatCapability) -> dict[str, object]:
    return {
        "name": item.name,
        "extension": item.extension,
        "available": item.available,
        "reason": item.reason,
    }


def capabilities_to_dict(value: ReadioCapabilities) -> dict[str, object]:
    """Serialize aggregate capabilities using readio.capabilities.v1."""
    return {
        "schema": "readio.capabilities.v1",
        "result": {
            "readio_version": value.readio_version,
            "specialists": [
                {
                    "package": item.package,
                    "version": item.version,
                    "state": item.state,
                    "reason": item.reason,
                }
                for item in value.specialists
            ],
            "workflow": {
                "conversion": value.workflow.conversion,
                "planning": value.workflow.planning,
                "synthesis": value.workflow.synthesis,
                "composition": value.workflow.composition,
                "export": value.workflow.export,
            },
            "engines": [_engine_info_to_dict(item) for item in value.engines],
            "export_formats": [_export_format_to_dict(item) for item in value.export_formats],
            "warnings": list(value.warnings),
        },
    }


def source_capabilities_to_dict(value: SourceCapabilities) -> dict[str, object]:
    """Serialize concrete-source capabilities using readio.source-capabilities.v1."""
    source = value.source
    return {
        "schema": "readio.source-capabilities.v1",
        "result": {
            "source": {
                "source": to_json_value(source.source),
                "kind": source.kind,
                "metadata": to_json_value(source.metadata),
                "chapters": to_json_value(source.chapters),
            },
            "chapter_selection": value.chapter_selection,
            "persistent_workspace": value.persistent_workspace,
            "default_output_format": value.default_output_format,
        },
    }


def synthesis_capabilities_to_dict(value: SynthesisCapabilities) -> dict[str, object]:
    """Serialize synthesis capabilities using readio.synthesis-capabilities.v1."""
    return {
        "schema": "readio.synthesis-capabilities.v1",
        "result": {
            "state": value.state,
            "engines": [_engine_info_to_dict(item) for item in value.engines],
            "reason": value.reason,
        },
    }


def export_capabilities_to_dict(
    formats: tuple[ExportFormatCapability, ...],
) -> dict[str, object]:
    """Serialize output-format capabilities using readio.export-capabilities.v1."""
    return {
        "schema": "readio.export-capabilities.v1",
        "result": {"formats": [_export_format_to_dict(item) for item in formats]},
    }


def serialize_public(value: Any) -> Any:
    """Select the versioned serializer for a public Readio result when available."""
    if isinstance(value, WorkflowPlan):
        return workflow_plan_to_dict(value)
    if isinstance(value, RenderResult):
        return render_result_to_dict(value)
    if isinstance(value, SpeakResult):
        return speak_result_to_dict(value)
    if isinstance(value, ErrorInfo):
        return error_info_to_dict(value)
    if isinstance(value, ReadioCapabilities):
        return capabilities_to_dict(value)
    if isinstance(value, SourceCapabilities):
        return source_capabilities_to_dict(value)
    if isinstance(value, SynthesisCapabilities):
        return synthesis_capabilities_to_dict(value)
    if isinstance(value, tuple) and all(isinstance(item, ExportFormatCapability) for item in value):
        return export_capabilities_to_dict(value)
    return to_json_value(value)


__all__ = [
    "capabilities_to_dict",
    "error_info_to_dict",
    "export_capabilities_to_dict",
    "render_result_to_dict",
    "speak_result_to_dict",
    "serialize_public",
    "source_capabilities_to_dict",
    "synthesis_capabilities_to_dict",
    "to_json_value",
    "workflow_plan_to_dict",
]
