from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

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
