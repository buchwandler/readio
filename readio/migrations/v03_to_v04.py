"""One-shot conversion helpers for persisted Readio v0.3 data."""

from __future__ import annotations

import os
import secrets
import shutil
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import config as config_internal
from ..config import ReaderSettings
from ..engines.registry import CANONICAL_ENGINE_IDS, engine_for_ssmd_provider, normalize_engine_id
from ..project import atomic_write_json, find_project, read_json
from ..project_model import DocumentIndex, DocumentScope, ProjectManifest
from ..role_targets import VoiceTarget, voice_target_from_mapping


class MigrationError(ValueError):
    """Raised when legacy persisted data cannot be migrated without guessing."""


def _legacy_engine(namespace: str) -> str:
    canonical = normalize_engine_id(namespace)
    if canonical in CANONICAL_ENGINE_IDS:
        return canonical
    return engine_for_ssmd_provider(namespace)


def _role_target(value: Any, role: str) -> VoiceTarget:
    try:
        return voice_target_from_mapping(value, name=f"roles.{role}")
    except (TypeError, ValueError) as exc:
        raise MigrationError(f"cannot migrate role {role!r}: {exc}") from exc


def migrate_config_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a v0.3 config mapping to the schema-3 engine-neutral shape."""
    schema = data.get("schema", 0)
    if schema == 3:
        config_internal._config_from_data(data)
        return dict(data)
    if not isinstance(schema, int) or isinstance(schema, bool) or schema not in {0, 1, 2}:
        raise MigrationError(
            f"unsupported configuration schema {schema!r}; expected v0.3 schema 0, 1, or 2"
        )

    migrated = dict(data)
    old_reader = migrated.get("reader")
    if old_reader is None:
        old_reader = {
            name: migrated.pop(name)
            for name in ReaderSettings.__dataclass_fields__
            if name in migrated
        }
    if not isinstance(old_reader, Mapping):
        raise MigrationError("legacy [reader] must be a table")
    reader = dict(old_reader)
    reader["engine"] = normalize_engine_id(str(reader.get("engine", "kokoro")))
    if reader["engine"] != "kokoro" and reader.get("voice") == "af_sarah":
        reader["voice"] = None
    short_sentence = reader.get("short_sentence")
    if isinstance(short_sentence, str) and short_sentence.strip().lower() == "auto":
        reader["short_sentence"] = "phrase"
    if reader.get("spacy") == "required":
        reader["spacy"] = "sm"

    raw_roles = migrated.get("roles", {})
    if not isinstance(raw_roles, Mapping):
        raise MigrationError("legacy [roles] must be a table")
    roles = {role: _role_target(value, str(role)) for role, value in raw_roles.items()}

    provider_tables = migrated.get("voices", {})
    if not isinstance(provider_tables, Mapping):
        raise MigrationError("legacy [voices] must be a table")
    candidates: dict[str, list[tuple[str, VoiceTarget]]] = {}
    for namespace, raw_provider in provider_tables.items():
        if not isinstance(namespace, str) or not isinstance(raw_provider, Mapping):
            raise MigrationError("legacy voice provider tables must be named tables")
        provider_roles = raw_provider.get("roles", {})
        if not isinstance(provider_roles, Mapping):
            raise MigrationError(f"legacy voices.{namespace}.roles must be a table")
        try:
            engine = _legacy_engine(namespace)
        except ValueError as exc:
            raise MigrationError(f"cannot migrate voice provider {namespace!r}: {exc}") from exc
        for role, voice in provider_roles.items():
            if (
                not isinstance(role, str)
                or not role.strip()
                or not isinstance(voice, str)
                or not voice.strip()
            ):
                raise MigrationError(
                    f"legacy voices.{namespace}.roles must map names to non-empty voice IDs"
                )
            candidates.setdefault(role, []).append((namespace, VoiceTarget(engine, voice)))

    for role, values in candidates.items():
        if role in roles:
            continue
        distinct = {target for _namespace, target in values}
        if len(distinct) != 1:
            namespaces = ", ".join(sorted(namespace for namespace, _target in values))
            raise MigrationError(
                f"role {role!r} has conflicting provider bindings ({namespaces}); resolve it in the v0.3 config before migration"
            )
        roles[role] = next(iter(distinct))

    language_values = migrated.get("languages", {})
    if not isinstance(language_values, Mapping):
        raise MigrationError("legacy [languages] must be a table")
    languages: dict[str, Any] = {}
    for language, raw_settings in language_values.items():
        if not isinstance(raw_settings, Mapping):
            raise MigrationError(f"legacy languages.{language} must be a table")
        settings = dict(raw_settings)
        if settings.get("engine") is not None:
            settings["engine"] = normalize_engine_id(str(settings["engine"]))
        languages[str(language)] = settings

    ssmd = migrated.get("ssmd", {})
    if not isinstance(ssmd, Mapping):
        raise MigrationError("legacy [ssmd] must be a table")
    ssmd = {key: value for key, value in ssmd.items() if key != "voice_provider"}

    result = dict(migrated)
    result.pop("voices", None)
    result["schema"] = 3
    result["reader"] = reader
    result["ssmd"] = dict(ssmd)
    result["languages"] = languages
    result["roles"] = {role: target.to_dict() for role, target in sorted(roles.items())}
    config_internal._config_from_data(result)
    return result


def migrate_config_file(path: Path | str | None = None) -> Path | None:
    """Safely migrate a v0.3 config in place and retain a side-by-side backup."""
    target = Path(path).expanduser() if path is not None else config_internal.config_path()
    if not target.is_file():
        raise MigrationError(f"configuration file does not exist: {target}")
    try:
        from ..config import tomllib

        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise MigrationError(f"cannot read legacy configuration {target}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise MigrationError("legacy config must contain a TOML table")
    if data.get("schema") == 3:
        config_internal._config_from_data(data)
        return None

    migrated = migrate_config_data(data)
    config = config_internal._config_from_data(migrated)
    backup = target.with_name(target.name + ".v03.bak")
    if backup.exists():
        raise MigrationError(f"migration backup already exists; refusing to overwrite it: {backup}")
    shutil.copy2(target, backup)
    # save_config writes to a same-directory temporary file and atomically replaces target.
    config_internal.save_config(config, target)
    return backup


def _safe_project_file(root: Path, value: Any, field: str, *, required: bool = False) -> Path:
    if not isinstance(value, str) or not value:
        raise MigrationError(f"legacy project {field} must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise MigrationError(f"legacy project {field} escapes the project root: {value!r}")
    path = root / relative
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise MigrationError(f"legacy project {field} escapes the project root: {value!r}")
    if required and not path.is_file():
        raise MigrationError(f"legacy project artifact is missing: {path}")
    return path


def _legacy_project_settings(settings: Any) -> dict[str, Any]:
    if not isinstance(settings, Mapping):
        raise MigrationError("legacy project settings must be an object")
    migrated = dict(settings)
    synthesis = migrated.get("synthesis")
    if isinstance(synthesis, Mapping):
        synthesis = dict(synthesis)
        if synthesis.get("engine") is not None:
            synthesis["engine"] = normalize_engine_id(str(synthesis["engine"]))
        migrated["synthesis"] = synthesis

    raw_ssmd = migrated.get("ssmd")
    if raw_ssmd is None:
        return migrated
    if not isinstance(raw_ssmd, Mapping):
        raise MigrationError("legacy project settings.ssmd must be an object")
    ssmd = dict(raw_ssmd)
    raw_bindings = ssmd.pop("voice_bindings", {})
    ssmd.pop("voice_provider", None)
    if not isinstance(raw_bindings, Mapping):
        raise MigrationError("legacy project voice_bindings must be an object")
    role_bindings = ssmd.get("role_bindings", {})
    if not isinstance(role_bindings, Mapping):
        raise MigrationError("project role_bindings must be an object")
    converted = {
        str(role): voice_target_from_mapping(value, name=f"project role_bindings.{role}")
        for role, value in role_bindings.items()
    }
    candidates: dict[str, list[VoiceTarget]] = {}
    for namespace, bindings in raw_bindings.items():
        if not isinstance(namespace, str) or not isinstance(bindings, Mapping):
            raise MigrationError(
                "legacy project voice_bindings must map provider names to role tables"
            )
        try:
            engine = _legacy_engine(namespace)
        except ValueError as exc:
            raise MigrationError(f"cannot migrate project provider {namespace!r}: {exc}") from exc
        for role, voice in bindings.items():
            if (
                not isinstance(role, str)
                or not role.strip()
                or not isinstance(voice, str)
                or not voice.strip()
            ):
                raise MigrationError(
                    f"legacy project voice_bindings.{namespace} must map roles to voice IDs"
                )
            candidates.setdefault(role, []).append(VoiceTarget(engine, voice))
    for role, values in candidates.items():
        if role in converted:
            continue
        distinct = set(values)
        if len(distinct) > 1:
            raise MigrationError(
                f"project role {role!r} has conflicting provider bindings; resolve it before migration"
            )
        converted[role] = next(iter(distinct))
    if converted:
        ssmd["role_bindings"] = {
            role: target.to_dict() for role, target in sorted(converted.items())
        }
    else:
        ssmd.pop("role_bindings", None)
    if ssmd:
        migrated["ssmd"] = ssmd
    else:
        migrated.pop("ssmd", None)
    return migrated


def _legacy_project_manifest(
    root: Path, data: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    schema = data.get("schema_version")
    if data.get("format") != "readio.project" or schema not in {1, 2}:
        raise MigrationError(
            f"unsupported project format/schema: {data.get('format')!r}/{schema!r}"
        )
    for field in ("project_id", "name"):
        if not isinstance(data.get(field), str) or not data[field]:
            raise MigrationError(f"legacy project {field} must be a non-empty string")
    source = data.get("source")
    document = data.get("document")
    plan = data.get("plan")
    synthesis = data.get("active_synthesis")
    composition = data.get("composition")
    for name, value in (
        ("source", source),
        ("document", document),
        ("plan", plan),
        ("active_synthesis", synthesis),
        ("composition", composition),
    ):
        if not isinstance(value, Mapping):
            raise MigrationError(f"legacy project {name} must be an object")
    _safe_project_file(root, source.get("path"), "source.path", required=True)
    if schema == 1:
        text_path = _safe_project_file(
            root, document.get("text_path"), "document.text_path", required=True
        )
        metadata_path = _safe_project_file(
            root, document.get("metadata_path"), "document.metadata_path", required=True
        )
        metadata = read_json(metadata_path)
        input_format = metadata.get("document_format")
        if not isinstance(input_format, str) or not input_format:
            input_format = "ssmd" if source.get("format") == "ssmd" else "text"
        scope = DocumentScope(
            id="document",
            kind="document",
            path=text_path.relative_to(root).as_posix(),
            input_format=input_format,
            title=str(data["name"]),
            extracted_sha256=metadata.get("document_sha256"),
        )
        index_path = "document/index.json"
        metadata_index = dict(metadata)
    else:
        index = document.get("index_path")
        index_path = str(index)
        index_file = _safe_project_file(root, index, "document.index_path", required=True)
        raw_index = read_json(index_file)
        DocumentIndex.from_dict(raw_index)
        scope = None
        metadata_index = {}

    for group, keys in (
        (plan, ("index_path",)),
        (synthesis, ("profile_path", "trace_path")),
        (composition, ("audiojob_path", "state_path", "master_path", "timeline_path")),
    ):
        for key in keys:
            _safe_project_file(root, group.get(key), f"{key}")

    migrated = dict(data)
    migrated["schema_version"] = 3
    migrated["kind"] = data.get("kind", "document") if schema == 2 else "document"
    migrated["document"] = {"index_path": index_path}
    migrated["settings"] = _legacy_project_settings(data.get("settings", {}))
    migrated["outputs"] = {}
    document_index_payload = None
    if scope is not None:
        document_index_payload = DocumentIndex(scopes=(scope,), metadata=metadata_index).to_dict()
    _safe_project_file(root, index_path, "document.index_path")
    ProjectManifest.from_dict(migrated)
    return migrated, document_index_payload


@contextmanager
def _project_migration_lock(root: Path) -> Iterator[None]:
    lock_path = root / ".lock"
    payload = f"{os.getpid()} project-migrate-v03-to-v04\n".encode()
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        owner = (
            lock_path.read_text(encoding="utf-8").strip() if lock_path.exists() else "unknown owner"
        )
        raise MigrationError(f"project is locked for another mutation ({owner})") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _restore_backup_file(saved: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.restore-{secrets.token_hex(6)}")
    try:
        shutil.copy2(saved, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_project(path: Path | str | None = None) -> Path | None:
    """Migrate one schema-1/2 project under lock with backup and rollback safety."""
    root = find_project(path)
    if root is None:
        raise MigrationError(f"not a Readio project: {path or Path.cwd()}")
    with _project_migration_lock(root):
        manifest_path = root / "project.json"
        original = read_json(manifest_path)
        schema = original.get("schema_version")
        if schema == 3:
            ProjectManifest.from_dict(original)
            return None
        migrated, document_index_payload = _legacy_project_manifest(root, original)
        backup = root / f".readio-v03-backup-{secrets.token_hex(6)}"
        backup.mkdir()
        shutil.copy2(manifest_path, backup / "project.json")
        stale_state_paths = (
            Path("synthesis/profile.json"),
            Path("synthesis/trace.json"),
            Path("composition/state.json"),
            Path("output/state.json"),
        )
        for relative in stale_state_paths:
            source = _safe_project_file(root, relative.as_posix(), str(relative))
            if source.is_symlink():
                raise MigrationError(
                    f"legacy project state artifact must not be a symlink: {source}"
                )
            if source.exists() and not source.is_file():
                raise MigrationError(
                    f"legacy project state artifact is not a regular file: {source}"
                )
            if source.is_file():
                destination = backup / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

        index_path: Path | None = None
        index_existed = False
        if document_index_payload is not None:
            document_data = migrated.get("document")
            if not isinstance(document_data, Mapping):
                raise MigrationError("migrated document index path is invalid")
            index_path = _safe_project_file(
                root, document_data.get("index_path"), "document.index_path"
            )
            if index_path.is_symlink():
                raise MigrationError(f"legacy document index must not be a symlink: {index_path}")
            if index_path.exists() and not index_path.is_file():
                raise MigrationError(f"legacy document index is not a regular file: {index_path}")
            index_existed = index_path.is_file()
            if index_existed:
                destination = backup / index_path.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(index_path, destination)

        try:
            if index_path is not None:
                atomic_write_json(index_path, document_index_payload)
            atomic_write_json(manifest_path, migrated)
            for relative in stale_state_paths:
                (root / relative).unlink(missing_ok=True)
        except Exception as error:
            try:
                _restore_backup_file(backup / "project.json", manifest_path)
                if index_path is not None:
                    if index_existed:
                        _restore_backup_file(backup / index_path.relative_to(root), index_path)
                    else:
                        index_path.unlink(missing_ok=True)
                for relative in stale_state_paths:
                    saved = backup / relative
                    if saved.is_file():
                        destination = root / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        _restore_backup_file(saved, destination)
            except Exception as rollback_error:
                raise MigrationError(
                    f"project migration failed and rollback was incomplete; recovery backup retained at {backup}"
                ) from rollback_error
            raise MigrationError(
                f"project migration failed; original data was restored and backup retained at {backup}: {error}"
            ) from error
    return backup


__all__ = ["MigrationError", "migrate_config_data", "migrate_config_file", "migrate_project"]
