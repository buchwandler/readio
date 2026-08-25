from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import ssmd as ssmd_api
import yaml

from .config import ReadioConfig
from .errors import SSMDInputError


class SSMDAuthoringError(SSMDInputError):
    code = "ssmd.authoring_invalid"


def executable() -> str:
    value = shutil.which("ssmd")
    if value is None:
        raise SSMDAuthoringError("ssmd executable not found on PATH")
    return value


def build_ssmd_config(cfg: ReadioConfig) -> dict[str, Any]:
    provider = cfg.ssmd.voice_provider
    settings = cfg.voices[provider]
    return {
        "schema": "ssmd.config.v1",
        "authoring": {
            "default_voice_provider": provider,
            "materialize": {
                "voice_bindings": "when-needed",
                "pause_defaults": "when-enabled",
            },
        },
        "voice_inventory": {provider: {voice: {"enabled": True} for voice in settings.ids}},
        "voice_bindings": {provider: dict(settings.roles)},
        "pause_defaults": {"enabled": False},
    }


def _error_detail(payload: Mapping[str, Any], stderr: str) -> str:
    candidates: list[Any] = [payload]
    nested = payload.get("result")
    if isinstance(nested, dict):
        candidates.append(nested)
        issues = nested.get("issues")
        if isinstance(issues, list):
            candidates.extend(issue for issue in issues if isinstance(issue, dict))
        files = nested.get("files")
        if isinstance(files, list):
            for item in files:
                if isinstance(item, dict):
                    file_issues = item.get("issues")
                    if isinstance(file_issues, list):
                        candidates.extend(issue for issue in file_issues if isinstance(issue, dict))
    for candidate in candidates:
        for key in ("error", "message", "detail"):
            value = candidate.get(key)
            if isinstance(value, str) and value:
                return value
    return stderr.strip() or "SSMD command failed"


def run_ssmd_json(args: Sequence[str], *, config_path: Path) -> dict[str, Any]:
    command = [executable(), "--json", "--config", str(config_path), *args]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        detail = completed.stderr.strip() or "SSMD returned invalid JSON"
        raise SSMDAuthoringError(f"SSMD validation failed: {detail}") from exc
    if not isinstance(payload, dict):
        raise SSMDAuthoringError("SSMD returned invalid JSON object")
    if completed.returncode != 0:
        raise SSMDAuthoringError(
            f"SSMD validation failed: {_error_detail(payload, completed.stderr)} "
            f"(exit code {completed.returncode})"
        )
    if payload.get("ok") is not True:
        raise SSMDAuthoringError(
            f"SSMD validation failed: {_error_detail(payload, completed.stderr)}"
        )
    return payload


def roundtrip_check(path: Path, cfg: ReadioConfig) -> Mapping[str, Any]:
    source = path.expanduser()
    config_path: Path | None = None
    prepared_path: Path | None = None
    provider = cfg.ssmd.voice_provider
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", prefix="readio-ssmd-authoring-", delete=False
        ) as handle:
            yaml.safe_dump(build_ssmd_config(cfg), handle, sort_keys=False)
            config_path = Path(handle.name)
        with tempfile.NamedTemporaryFile(
            suffix=".ssmd", prefix="readio-ssmd-authoring-", delete=False
        ) as handle:
            prepared_path = Path(handle.name)
        prepared_path.unlink()
        create_args = [
            "create",
            str(source),
            "-o",
            str(prepared_path),
            "--voice-provider",
            provider,
        ]
        if cfg.ssmd.fail_on_warn:
            create_args.append("--fail-on-warn")
        run_ssmd_json(create_args, config_path=config_path)
        lint_args = ["lint", str(prepared_path), "--voice-provider", provider]
        if cfg.ssmd.fail_on_warn:
            lint_args.append("--fail-on-warn")
        lint_args.append("--roundtrip")
        return run_ssmd_json(lint_args, config_path=config_path)
    finally:
        if config_path is not None:
            config_path.unlink(missing_ok=True)
        if prepared_path is not None:
            prepared_path.unlink(missing_ok=True)


def materialize_voice_bindings(
    source: Path,
    bindings: Mapping[str, str],
    *,
    provider: str,
    output: Path | None = None,
    in_place: bool = False,
) -> Path:
    """Write explicit SSMD bindings without invoking the SSMD authoring CLI."""
    source = source.expanduser()
    if not bindings:
        raise SSMDAuthoringError("at least one --voice-bind value is required")
    if in_place and output is not None and output.expanduser() != source:
        raise SSMDAuthoringError("--in-place cannot be combined with a different output path")
    target = source if in_place else (output or source.with_name(f"{source.stem}.bound{source.suffix}"))
    target = target.expanduser()
    if target == source and not in_place:
        raise SSMDAuthoringError("refusing to overwrite the SSMD source; use --in-place explicitly")
    if target.exists() and target != source:
        raise SSMDAuthoringError(f"output already exists: {target}; choose another path")
    try:
        front_matter = ssmd_api.parse_front_matter(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SSMDAuthoringError(f"invalid SSMD source: {exc}") from exc
    generated = {"voice_bindings": {provider: dict(bindings)}}
    merged = ssmd_api.merge_generated_header(front_matter.data, generated)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        ssmd_api.serialize_front_matter(merged, front_matter.body),
        encoding="utf-8",
    )
    return target
