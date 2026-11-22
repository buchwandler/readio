"""Read-only capability aggregation for Readio integrations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from readio import __version__
from readio._packages import SPECIALIST_PACKAGES, distribution_version
from readio.api.types import (
    CapabilityState,
    ExportFormatCapability,
    ReadioCapabilities,
    SourceCapabilities,
    SourceInspection,
    SpecialistCapability,
    SynthesisCapabilities,
    WorkflowCapabilities,
)
from readio.integrations.audioexport import preflight_export, supported_formats


def _specialist_capability(package: str) -> SpecialistCapability:
    try:
        package_version = distribution_version(package)
    except Exception as error:
        return SpecialistCapability(
            package=package,
            version=None,
            state="unknown",
            reason=f"Cannot inspect package metadata: {error}",
        )
    if package_version is None:
        return SpecialistCapability(
            package=package,
            version=None,
            state="unavailable",
            reason="Distribution is not installed.",
        )
    return SpecialistCapability(package, package_version, "ready")


def source_capabilities(service: Any, source: str | Path) -> SourceCapabilities:
    inspection: SourceInspection = service.inspect_source(source)
    return SourceCapabilities(
        source=inspection,
        chapter_selection=inspection.kind == "book",
        persistent_workspace=True,
        default_output_format="m4b" if inspection.kind == "book" else "mp3",
    )


def synthesis_capabilities(service: Any) -> SynthesisCapabilities:
    specialist = _specialist_capability("voicerender")
    if specialist.state != "ready":
        return SynthesisCapabilities(specialist.state, (), specialist.reason)
    try:
        engines = service.list_engines()
    except Exception as error:
        return SynthesisCapabilities("unknown", (), f"Cannot list synthesis engines: {error}")
    if any(engine.runnable for engine in engines):
        return SynthesisCapabilities("ready", engines)
    if engines:
        return SynthesisCapabilities(
            "unavailable", engines, "No installed synthesis engine is runnable."
        )
    return SynthesisCapabilities("unavailable", (), "VoiceRender reported no synthesis engines.")


def _collect_export_formats() -> tuple[tuple[ExportFormatCapability, ...], str | None]:
    try:
        formats = supported_formats()
    except Exception as error:
        return (), str(error)

    results: list[ExportFormatCapability] = []
    for name, specification in sorted(formats.items()):
        extension = getattr(specification, "extension", None)
        if (
            not isinstance(name, str)
            or not isinstance(extension, str)
            or not extension.startswith(".")
        ):
            results.append(
                ExportFormatCapability(
                    name=str(name),
                    extension=extension if isinstance(extension, str) else "",
                    available=False,
                    reason="AudioExport returned an invalid format specification.",
                )
            )
            continue
        try:
            preflight_export(name)
        except Exception as error:
            results.append(ExportFormatCapability(name, extension, False, str(error)))
        else:
            results.append(ExportFormatCapability(name, extension, True))
    return tuple(results), None


def export_capabilities() -> tuple[ExportFormatCapability, ...]:
    formats, _error = _collect_export_formats()
    return formats


def collect_capabilities(service: Any) -> ReadioCapabilities:
    specialists = tuple(_specialist_capability(package) for package in SPECIALIST_PACKAGES)
    by_package = {specialist.package: specialist for specialist in specialists}
    synthesis = synthesis_capabilities(service)
    formats, format_error = _collect_export_formats()
    warnings = [
        f"{specialist.package}: {specialist.reason}"
        for specialist in specialists
        if specialist.reason is not None
    ]
    if format_error is not None:
        warnings.append(f"AudioExport formats are unavailable: {format_error}")
    elif formats and not any(item.available for item in formats):
        warnings.append("No AudioExport format passed readiness preflight.")

    def state(package: str) -> CapabilityState:
        return by_package[package].state

    export_state = (
        "ready"
        if any(item.available for item in formats)
        else "unavailable"
        if state("audioexport") == "ready"
        else state("audioexport")
    )
    workflow = WorkflowCapabilities(
        conversion=state("ssmdconvert"),
        planning=state("utterplan"),
        synthesis=synthesis.state,
        composition=state("audiocompose"),
        export=export_state,
    )
    return ReadioCapabilities(
        readio_version=__version__,
        specialists=specialists,
        workflow=workflow,
        engines=synthesis.engines,
        export_formats=formats,
        warnings=tuple(warnings),
    )


__all__ = [
    "collect_capabilities",
    "export_capabilities",
    "source_capabilities",
    "synthesis_capabilities",
]
