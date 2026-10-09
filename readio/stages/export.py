"""Encoding stage for persistent Readio projects."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from ..audioio import read_audio
from ..formats import (
    SUPPORTED_AUDIO_FORMATS,
    AudioFormat,
    ensure_audio_format_available,
    normalize_audio_output_path,
)
from ..integrations.audioexport import (
    AudioExportIntegrationError,
    ResolvedAudioExport,
    encode_resolved,
    load_audioexport,
    resolve_profile_export,
    resolve_profile_output_row,
)
from ..project import Project, atomic_write_json, canonical_json, hash_file, project_lock, read_json
from ..wave import atomic_audio_path, create_audio_sink

_M4A_DEFAULT_BITRATE = "192k"
_OPUS_DEFAULT_BITRATE = "96k"
_BITRATE_FORMATS = frozenset({"m4a", "opus"})
_BITRATE_PATTERN = re.compile(r"^(\d+)\s*([kKmM]?)$")
_EXPORT_STATE_PATH = Path("output/state.json")


@dataclass(frozen=True, slots=True)
class _BatchOutputPlan:
    format: str
    target: Path
    export_id: str
    options: Mapping[str, Any]
    generic: ResolvedAudioExport | None = None
    audiobook_profile: Any | None = None
    audiobook_spec: Any | None = None
    audiobook_prepared: Any | None = None
    metadata: Mapping[str, str] | None = None
    current: bool = False
    replace_authorized: bool = False


def export_id(payload: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def normalize_bitrate(value: str) -> str:
    """Normalize an integer bitrate in bits/s, kbit/s, or Mbit/s to kbit/s."""
    match = _BITRATE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"invalid bitrate {value!r}; use an integer such as '96k'")
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if amount <= 0:
        raise ValueError("bitrate must be greater than zero")
    if unit == "m":
        amount *= 1000
    elif unit == "":
        amount = max(1, round(amount / 1000))
    return f"{amount}k"


def effective_audio_export_options(
    audio_format: AudioFormat, bitrate: str | None
) -> dict[str, str]:
    if bitrate is not None and audio_format not in _BITRATE_FORMATS:
        raise ValueError(f"bitrate is not supported for {audio_format.upper()} output")
    if audio_format not in _BITRATE_FORMATS:
        return {}
    default = _M4A_DEFAULT_BITRATE if audio_format == "m4a" else _OPUS_DEFAULT_BITRATE
    return {"bitrate": normalize_bitrate(bitrate) if bitrate is not None else default}


def build_audio_export_identity(
    *,
    master_sha256: str,
    audio_format: AudioFormat,
    effective_options: Mapping[str, str],
) -> str:
    return export_id(
        {
            "schema": "readio.export.v2",
            "master_sha256": master_sha256,
            "format": audio_format,
            "options": dict(effective_options),
        }
    )


def target_record(project: Project, target: Path) -> str:
    resolved = target.expanduser().resolve()
    try:
        return resolved.relative_to(project.state_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _load_output_records(project: Project) -> dict[str, dict[str, Any]]:
    state_path = project.state_root / _EXPORT_STATE_PATH
    if not state_path.is_file():
        return {}
    state = read_json(state_path)
    if state.get("format") == "readio.export-index":
        outputs = state.get("outputs", {})
        if not isinstance(outputs, dict):
            return {}
        return {str(key): value for key, value in outputs.items() if isinstance(value, dict)}
    # Read the previous one-output schema so existing artifacts can be safely
    # recognized and migrated on their next successful export.
    if state.get("format") == "readio.export-state" and isinstance(state.get("path"), str):
        return {state["path"]: state}
    return {}


def project_export_states(project: Project) -> tuple[dict[str, Any], ...]:
    return tuple(_load_output_records(project).values())


def output_state_for(project: Project, target: Path) -> dict[str, Any] | None:
    return _load_output_records(project).get(target_record(project, target))


def store_export_state(project: Project, target: Path, state: Mapping[str, Any]) -> None:
    outputs = _load_output_records(project)
    outputs[target_record(project, target)] = dict(state)
    atomic_write_json(
        project.state_root / _EXPORT_STATE_PATH,
        {"format": "readio.export-index", "schema_version": 2, "outputs": outputs},
    )


def _profile_effective_options(resolved: ResolvedAudioExport) -> dict[str, Any]:
    if resolved.format == "wav":
        return {"backend": "readio", "format": "wav"}
    report = resolved.audioexport.doctor()
    tools = report.get("tools", {}) if isinstance(report, Mapping) else {}
    tool_versions: dict[str, str | None] = {}
    if isinstance(tools, Mapping):
        for name in ("ffmpeg", "ffprobe"):
            tool = tools.get(name)
            tool_versions[name] = (
                str(tool["version"])
                if isinstance(tool, Mapping) and tool.get("version") is not None
                else None
            )
    return {
        "backend": "audioexport",
        "audioexport_version": resolved.version,
        "format": resolved.format,
        "bitrate": resolved.bitrate,
        "metadata": dict(resolved.metadata),
        "cover_sha256": hash_file(resolved.cover) if resolved.cover is not None else None,
        "timeline_sha256": (
            hash_file(resolved.timeline) if resolved.timeline is not None else None
        ),
        "tool_versions": tool_versions,
    }


def build_profile_audio_export_identity(
    *,
    master_sha256: str,
    resolved: ResolvedAudioExport,
    effective_options: Mapping[str, Any] | None = None,
) -> str:
    options = (
        dict(effective_options)
        if effective_options is not None
        else _profile_effective_options(resolved)
    )
    return export_id(
        {
            "schema": "readio.export.v3",
            "master_sha256": master_sha256,
            "format": resolved.format,
            "options": options,
        }
    )


def _profile_export_target(
    project: Project, output: Path | None, resolved: ResolvedAudioExport
) -> Path:
    if output is not None:
        target = Path(output).expanduser()
    elif resolved.output_spec.filename is not None:
        target = project.state_root / "output" / resolved.filename
    else:
        target = project.state_root / "output" / f"{project.manifest.name}.{resolved.format}"
    if not target.is_absolute():
        target = project.state_root / target
    if resolved.format != "wav":
        target = normalize_audio_output_path(target, cast(AudioFormat, resolved.format))
    return target.resolve()


def resolve_project_export_target(
    project: Project,
    *,
    audio_format: AudioFormat,
    bitrate: str | None,
    output: Path | None,
    profile: Path | None,
    format_explicit: bool,
) -> Path:
    """Resolve the same output path used by direct and profile-backed exports."""
    if profile is not None:
        resolved = resolve_profile_export(
            profile,
            master=project.paths["composition_master"],
            source_stem=project.manifest.name,
            requested_format=audio_format,
            format_explicit=format_explicit,
            bitrate_override=bitrate,
            allowed_formats=SUPPORTED_AUDIO_FORMATS,
            preflight=False,
        )
        return _profile_export_target(project, output, resolved)

    target = output or project.state_root / "output" / f"{project.manifest.name}.{audio_format}"
    if not target.is_absolute():
        target = project.state_root / target
    return target.resolve()


def _batch_output_target(project: Project, out_dir: Path, filename: str, audio_format: str) -> Path:
    output_root = Path(out_dir).expanduser().resolve()
    target = output_root / filename
    if not target.is_absolute():
        target = project.state_root / target
    if audio_format == "m4b":
        from .audiobook_export import audiobook_export_target

        target = audiobook_export_target(project, target)
    else:
        if audio_format != "wav":
            target = normalize_audio_output_path(target, cast(AudioFormat, audio_format))
        target = target.resolve()
    try:
        target.relative_to(output_root)
    except ValueError as error:
        raise AudioExportIntegrationError(
            f"profile output filename escapes the batch output directory: {filename}",
            code="readio.export.output_directory_escape",
        ) from error
    return target


def _batch_output_current(project: Project, plan: _BatchOutputPlan) -> bool:
    if not plan.target.is_file():
        return False
    if plan.audiobook_prepared is not None:
        from .audiobook_export import is_audiobook_export_current

        return is_audiobook_export_current(project, plan.target, plan.audiobook_prepared)
    state = output_state_for(project, plan.target)
    backend = "readio" if plan.format == "wav" else "audioexport"
    return (
        state is not None
        and state.get("export_id") == plan.export_id
        and state.get("audio_format") == plan.format
        and state.get("backend") == backend
        and state.get("path") == target_record(project, plan.target)
        and state.get("output_sha256") == hash_file(plan.target)
    )


def is_export_current(
    project: Project,
    target: Path,
    *,
    audio_format: AudioFormat,
    bitrate: str | None = None,
    profile: Path | None = None,
    format_explicit: bool = True,
) -> bool:
    if not target.is_file():
        return False
    state = output_state_for(project, target)
    if state is None:
        return False
    master = project.paths["composition_master"]
    if not master.is_file():
        return False
    master_sha = hash_file(master)
    if profile is None:
        effective_options = effective_audio_export_options(audio_format, bitrate)
        expected_id = build_audio_export_identity(
            master_sha256=master_sha,
            audio_format=audio_format,
            effective_options=effective_options,
        )
        expected_backend = None
    else:
        resolved = resolve_profile_export(
            profile,
            master=master,
            source_stem=project.manifest.name,
            requested_format=audio_format,
            format_explicit=format_explicit,
            bitrate_override=bitrate,
            allowed_formats=SUPPORTED_AUDIO_FORMATS,
            preflight=False,
        )
        expected_id = build_profile_audio_export_identity(
            master_sha256=master_sha, resolved=resolved
        )
        expected_backend = "readio" if resolved.format == "wav" else "audioexport"
        audio_format = cast(AudioFormat, resolved.format)
    return (
        state.get("export_id") == expected_id
        and state.get("audio_format") == audio_format
        and state.get("path") == target_record(project, target)
        and (expected_backend is None or state.get("backend") == expected_backend)
        and state.get("output_sha256") == hash_file(target)
    )


def _can_replace_target(project: Project, target: Path, *, force: bool) -> bool:
    if force or not target.exists():
        return force
    previous = output_state_for(project, target)
    if (
        previous is not None
        and previous.get("path") == target_record(project, target)
        and previous.get("output_sha256") == hash_file(target)
    ):
        return True
    raise FileExistsError(
        f"output already exists and is not an unchanged Readio export: {target}; use --force to replace it"
    )


def export_project(
    project: Project,
    *,
    audio_format: AudioFormat = "wav",
    bitrate: str | None = None,
    output: Path | None = None,
    force: bool = False,
    profile: Path | None = None,
    format_explicit: bool = True,
) -> dict[str, Any]:
    with project_lock(project, operation="export"):
        if profile is None:
            ensure_audio_format_available(audio_format)
        master = project.paths["composition_master"]
        if not master.is_file():
            raise ValueError("composition master is missing; run readio compose first")
        master_sha = hash_file(master)
        profile_export: ResolvedAudioExport | None = None
        audioexport_result: Any | None = None
        if profile is None:
            selected_format = audio_format
            options = effective_audio_export_options(selected_format, bitrate)
            identity = build_audio_export_identity(
                master_sha256=master_sha,
                audio_format=selected_format,
                effective_options=options,
            )
            target = (
                output
                or project.state_root / "output" / f"{project.manifest.name}.{selected_format}"
            )
            target = Path(target).expanduser()
            if not target.is_absolute():
                target = project.state_root / target
            target = target.resolve()
        else:
            profile_export = resolve_profile_export(
                Path(profile),
                master=master,
                source_stem=project.manifest.name,
                requested_format=audio_format,
                format_explicit=format_explicit,
                bitrate_override=bitrate,
                allowed_formats=SUPPORTED_AUDIO_FORMATS,
            )
            selected_format = cast(AudioFormat, profile_export.format)
            if selected_format == "wav":
                ensure_audio_format_available(selected_format)
            options = _profile_effective_options(profile_export)
            identity = build_profile_audio_export_identity(
                master_sha256=master_sha,
                resolved=profile_export,
                effective_options=options,
            )
            target = _profile_export_target(project, output, profile_export)

        target.parent.mkdir(parents=True, exist_ok=True)
        replace_existing = _can_replace_target(project, target, force=force)
        if profile_export is not None and selected_format != "wav":
            audioexport_result = encode_resolved(
                master,
                target,
                profile_export,
                force_authorized=replace_existing,
            )
        else:
            audio, rate = read_audio(master)
            with (
                atomic_audio_path(target, force=replace_existing) as temporary,
                create_audio_sink(
                    temporary, selected_format, bitrate=options.get("bitrate")
                ) as sink,
            ):
                sink.write(audio, rate)

        record = target_record(project, target)
        state: dict[str, Any] = {
            "format": "readio.export-state",
            "schema_version": 3 if profile_export is not None else 2,
            "export_id": identity,
            "master_sha256": master_sha,
            "audio_format": selected_format,
            "options": options,
            "path": record,
            "output_sha256": hash_file(target),
        }
        if profile_export is not None:
            state.update(
                {
                    "backend": "readio" if selected_format == "wav" else "audioexport",
                    "profile_path": str(profile_export.profile_path),
                }
            )
            if audioexport_result is not None:
                state["audioexport_version"] = profile_export.version
                state["audioexport_export_id"] = audioexport_result.export_id
                state["audioexport_sidecar"] = str(audioexport_result.manifest_path)
        store_export_state(project, target, state)
        result = {
            "export_id": state["export_id"],
            "path": target,
            "format": selected_format,
            "output_sha256": state["output_sha256"],
        }
        if audioexport_result is not None:
            result["audioexport_export_id"] = audioexport_result.export_id
            result["reused"] = audioexport_result.reused
        return result


def export_profile_batch(
    project: Project,
    *,
    profile: Path,
    out_dir: Path | None = None,
    bitrate: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Preflight and Readio-track every output in one AudioExport profile."""
    from . import audiobook_export as audiobook_stage

    with project_lock(project, operation="export"):
        master = project.paths["composition_master"]
        if not master.is_file():
            raise ValueError("composition master is missing; run readio compose first")
        master_sha = hash_file(master)
        audioexport = load_audioexport()
        profile_path = Path(profile).expanduser().resolve()
        try:
            loaded_profile = audioexport.load_profile(profile_path)
        except audioexport.AudioExportError as error:
            details = getattr(error, "details", {})
            raise AudioExportIntegrationError(
                str(error),
                code=getattr(error, "code", "audioexport.profile_invalid"),
                details=details if isinstance(details, Mapping) else {},
            ) from error
        specs = tuple(loaded_profile.outputs)
        if not specs:
            raise AudioExportIntegrationError(
                "AudioExport profile has no output rows",
                code="readio.export.profile_empty",
            )
        m4b_specs = tuple(spec for spec in specs if spec.format == "m4b")
        if m4b_specs and project.manifest.kind != "audiobook":
            raise AudioExportIntegrationError(
                "M4B profile outputs require an audiobook project with a verified composition timeline",
                code="audiobook.export.not_audiobook",
            )
        if m4b_specs:
            verified_timeline = project.paths["composition_timeline"].resolve()
            if (
                loaded_profile.timeline is not None
                and Path(loaded_profile.timeline).resolve() != verified_timeline
            ):
                raise AudioExportIntegrationError(
                    "audiobook profile timeline conflicts with Readio's verified composition timeline",
                    code="audiobook.export.timeline_stale",
                )

        output_root = Path(out_dir or (project.state_root / "output")).expanduser()
        if not output_root.is_absolute():
            output_root = project.state_root / output_root
        output_root = output_root.resolve()
        if output_root.exists() and not output_root.is_dir():
            raise AudioExportIntegrationError(
                f"batch output directory is not a directory: {output_root}",
                code="readio.export.output_directory_invalid",
            )

        plans: list[_BatchOutputPlan] = []
        for spec in specs:
            if spec.format == "m4b":
                try:
                    resolved_audiobook = audiobook_stage._resolve_audiobook_profile(
                        project,
                        profile_path,
                        title=None,
                        author=None,
                        cover=None,
                        bitrate=bitrate,
                        selected_output=spec,
                        loaded_profile=loaded_profile,
                    )
                    prepared = audiobook_stage.prepare_audiobook_export(
                        project,
                        title=resolved_audiobook.metadata.get("title"),
                        author=resolved_audiobook.metadata.get("artist"),
                        cover=resolved_audiobook.resolved_output.cover,
                        bitrate=resolved_audiobook.resolved_output.bitrate,
                    )
                    metadata_tags = dict(resolved_audiobook.metadata)
                    metadata_tags["title"] = prepared.metadata.title
                    if prepared.metadata.author is None:
                        metadata_tags.pop("artist", None)
                    else:
                        metadata_tags["artist"] = prepared.metadata.author
                    prepared = replace(
                        prepared,
                        export_id=audiobook_stage._profile_audiobook_export_identity(
                            prepared, resolved_audiobook, metadata_tags
                        ),
                    )
                    profile_for_preflight = replace(
                        resolved_audiobook.profile, metadata=metadata_tags
                    )
                    try:
                        preflight_outputs = audioexport.preflight_profile(
                            profile_for_preflight, prepared.master
                        )
                    except audioexport.AudioExportError as error:
                        try:
                            audiobook_stage._raise_audioexport_audiobook_error(
                                error, phase="preflight"
                            )
                        except audiobook_stage.AudiobookExportError as mapped:
                            raise AudioExportIntegrationError(
                                str(mapped), code=mapped.code, details=mapped.details
                            ) from error
                    if len(preflight_outputs) != 1:
                        raise AudioExportIntegrationError(
                            "AudioExport preflight returned an unexpected M4B output count",
                            code="audiobook.export.profile_invalid",
                        )
                    checked = preflight_outputs[0]
                    if (
                        checked.format != "m4b"
                        or checked.timeline != verified_timeline
                        or checked.cover != prepared.metadata.cover
                    ):
                        raise AudioExportIntegrationError(
                            "AudioExport preflight did not preserve Readio's verified M4B inputs",
                            code="audiobook.export.timeline_stale",
                        )
                    resolved_audiobook = replace(
                        resolved_audiobook,
                        profile=profile_for_preflight,
                        resolved_output=checked,
                        metadata=metadata_tags,
                    )
                    target = _batch_output_target(project, output_root, checked.filename, "m4b")
                    plans.append(
                        _BatchOutputPlan(
                            format="m4b",
                            target=target,
                            export_id=prepared.export_id,
                            options={
                                "backend": "audioexport",
                                "audioexport_version": str(
                                    resolved_audiobook.audioexport.__version__
                                ),
                                "bitrate": prepared.bitrate,
                                "metadata": metadata_tags,
                                "cover_sha256": prepared.metadata.cover_sha256,
                                "timeline_sha256": prepared.timeline_sha256,
                            },
                            audiobook_profile=resolved_audiobook,
                            audiobook_spec=spec,
                            audiobook_prepared=prepared,
                            metadata=metadata_tags,
                        )
                    )
                except audiobook_stage.AudiobookExportError as error:
                    raise AudioExportIntegrationError(
                        str(error), code=error.code, details=error.details
                    ) from error
                continue

            resolved = resolve_profile_output_row(
                profile_path,
                loaded_profile,
                spec,
                master=master,
                source_stem=project.manifest.name,
                bitrate_override=bitrate,
                allowed_formats=SUPPORTED_AUDIO_FORMATS,
            )
            effective_options = _profile_effective_options(resolved)
            identity = build_profile_audio_export_identity(
                master_sha256=master_sha,
                resolved=resolved,
                effective_options=effective_options,
            )
            target = _batch_output_target(project, output_root, resolved.filename, resolved.format)
            plans.append(
                _BatchOutputPlan(
                    format=resolved.format,
                    target=target,
                    export_id=identity,
                    options=effective_options,
                    generic=resolved,
                )
            )

        protected_targets = {path.expanduser().resolve() for path in project.paths.values()}
        protected_targets.update(
            {
                profile_path.resolve(),
                (project.state_root / _EXPORT_STATE_PATH).resolve(),
            }
        )
        for plan in plans:
            resources: tuple[Path | None, ...] = ()
            if plan.generic is not None:
                resources = (plan.generic.cover, plan.generic.timeline)
            elif plan.audiobook_prepared is not None:
                resources = (
                    plan.audiobook_prepared.master,
                    project.paths["composition_timeline"],
                    plan.audiobook_prepared.metadata.cover,
                )
            protected_targets.update(
                resource.expanduser().resolve() for resource in resources if resource is not None
            )

        seen_targets: set[Path] = set()
        for plan in plans:
            if plan.target in protected_targets:
                raise AudioExportIntegrationError(
                    f"profile output conflicts with a Readio input or state file: {plan.target}",
                    code="readio.export.profile_output_collision",
                )
            if plan.target in seen_targets:
                raise AudioExportIntegrationError(
                    f"multiple profile outputs resolve to the same target: {plan.target}",
                    code="readio.export.profile_output_collision",
                )
            seen_targets.add(plan.target)

        checked_plans: list[_BatchOutputPlan] = []
        for plan in plans:
            replace_authorized = _can_replace_target(project, plan.target, force=force)
            current = not force and _batch_output_current(project, plan)
            checked_plans.append(
                replace(
                    plan,
                    current=current,
                    replace_authorized=replace_authorized,
                )
            )

        results: list[dict[str, Any]] = []
        for index, plan in enumerate(checked_plans):
            if plan.current:
                state = output_state_for(project, plan.target)
                results.append(
                    {
                        "format": plan.format,
                        "path": plan.target,
                        "status": "reused",
                        "export_id": plan.export_id,
                        "output_sha256": state["output_sha256"] if state else None,
                    }
                )
                continue
            try:
                if plan.audiobook_profile is not None:
                    raw = audiobook_stage.export_audiobook_project(
                        project,
                        output=plan.target,
                        bitrate=bitrate,
                        force=force,
                        profile=profile_path,
                        selected_profile_output=plan.audiobook_spec,
                        loaded_profile=loaded_profile,
                        lock_held=True,
                    )
                    results.append(
                        {
                            "format": plan.format,
                            "path": plan.target,
                            "status": "encoded",
                            "export_id": raw["export_id"],
                            "output_sha256": raw["output_sha256"],
                        }
                    )
                    continue

                assert plan.generic is not None
                plan.target.parent.mkdir(parents=True, exist_ok=True)
                audioexport_result = None
                if plan.format == "wav":
                    audio, rate = read_audio(master)
                    with (
                        atomic_audio_path(plan.target, force=plan.replace_authorized) as temporary,
                        create_audio_sink(
                            temporary,
                            cast(AudioFormat, plan.format),
                            bitrate=plan.options.get("bitrate"),
                        ) as sink,
                    ):
                        sink.write(audio, rate)
                else:
                    audioexport_result = encode_resolved(
                        master,
                        plan.target,
                        plan.generic,
                        force_authorized=plan.replace_authorized,
                    )
                state: dict[str, Any] = {
                    "format": "readio.export-state",
                    "schema_version": 3,
                    "export_id": plan.export_id,
                    "master_sha256": master_sha,
                    "audio_format": plan.format,
                    "options": dict(plan.options),
                    "path": target_record(project, plan.target),
                    "output_sha256": hash_file(plan.target),
                    "backend": "readio" if plan.format == "wav" else "audioexport",
                    "profile_path": str(profile_path),
                }
                if audioexport_result is not None:
                    state["audioexport_version"] = plan.generic.version
                    state["audioexport_export_id"] = audioexport_result.export_id
                    state["audioexport_sidecar"] = str(audioexport_result.manifest_path)
                store_export_state(project, plan.target, state)
                results.append(
                    {
                        "format": plan.format,
                        "path": plan.target,
                        "status": (
                            "reused"
                            if audioexport_result is not None and audioexport_result.reused
                            else "encoded"
                        ),
                        "export_id": plan.export_id,
                        "output_sha256": state["output_sha256"],
                    }
                )
            except Exception as error:  # noqa: BLE001 - retain ordered partial results for any output failure.
                results.append(
                    {
                        "format": plan.format,
                        "path": plan.target,
                        "status": "failed",
                        "export_id": plan.export_id,
                        "output_sha256": None,
                        "error_code": getattr(error, "code", "readio.export.batch_output_failed"),
                        "error_message": str(error),
                    }
                )
                results.extend(
                    {
                        "format": pending.format,
                        "path": pending.target,
                        "status": "not_attempted",
                        "export_id": pending.export_id,
                        "output_sha256": None,
                    }
                    for pending in checked_plans[index + 1 :]
                )
                break
        return {
            "success": all(item["status"] != "failed" for item in results),
            "outputs": results,
        }


__all__ = [
    "build_audio_export_identity",
    "build_profile_audio_export_identity",
    "effective_audio_export_options",
    "export_id",
    "export_project",
    "is_export_current",
    "normalize_bitrate",
    "output_state_for",
    "project_export_states",
    "store_export_state",
    "target_record",
]
