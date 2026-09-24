from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_MODULES = {
    "audio",
    "backends",
    "config",
    "document",
    "engines",
    "errors",
    "execution",
    "formats",
    "lexicons",
    "manifest",
    "models",
    "paths",
    "plan",
    "project",
    "reader",
    "spotify",
    "synthesis",
    "voices",
    "wave",
}
CLI_API_CONSUMERS = ("readio.cli", "readio.spotify_cli", "readio.cli_adapter")
ROOT = Path(__file__).resolve().parents[1]


def _consumer_path(module_name: str) -> Path:
    return ROOT.joinpath(*module_name.split(".")).with_suffix(".py")


def _existing_cli_consumers() -> tuple[str, ...]:
    return tuple(module for module in CLI_API_CONSUMERS if _consumer_path(module).is_file())


def _imported_modules(source: str, module_name: str) -> set[str]:
    tree = ast.parse(source)
    package = module_name.rpartition(".")[0].split(".")
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                if node.module:
                    base.extend(node.module.split("."))
                module = ".".join(base)
            else:
                module = node.module or ""
            if node.module is None and node.level:
                imported.update(
                    f"{module}.{alias.name}" if module else alias.name for alias in node.names
                )
            else:
                imported.add(module)
    return imported


def _forbidden_imports(module_name: str) -> set[str]:
    source = _consumer_path(module_name).read_text(encoding="utf-8")
    imported = _imported_modules(source, module_name)
    return {
        name
        for name in imported
        if name.startswith("readio.") and name.split(".")[1] in FORBIDDEN_MODULES
    }


def _consumer_trees() -> dict[str, ast.Module]:
    return {
        module_name: ast.parse(_consumer_path(module_name).read_text(encoding="utf-8"))
        for module_name in _existing_cli_consumers()
    }


def test_cli_modules_import_application_operations_from_public_api() -> None:
    violations = {
        module_name: sorted(_forbidden_imports(module_name))
        for module_name in _existing_cli_consumers()
        if _forbidden_imports(module_name)
    }

    assert not violations, (
        "CLI modules must consume domain operations through readio.api; "
        f"direct implementation imports found: {violations}"
    )


def test_cli_modules_do_not_construct_domain_public_errors() -> None:
    violations = []
    for module_name, tree in _consumer_trees().items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "VoiceResolutionError"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "public_api"
            ):
                violations.append(f"{module_name}:{node.lineno}")

    assert not violations, f"CLI modules must delegate domain error construction: {violations}"


def test_cli_modules_do_not_use_error_details_for_planning_control_flow() -> None:
    violations = []
    for module_name, tree in _consumer_trees().items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "details"
                and isinstance(node.value, ast.Name)
                and node.value.id == "error"
            ):
                violations.append(f"{module_name}:{node.lineno}")

    assert not violations, f"CLI modules must not branch on generic error details: {violations}"


def test_cli_progress_does_not_use_internal_composition_messages() -> None:
    internal_messages = {"compose_started", "compose_completed"}
    violations = []
    for module_name, tree in _consumer_trees().items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            strings = {
                child.value
                for child in ast.walk(node)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            }
            if not strings.intersection(internal_messages):
                continue
            if any(
                isinstance(child, ast.Attribute)
                and child.attr == "message"
                and isinstance(child.value, ast.Name)
                and child.value.id == "event"
                for child in ast.walk(node)
            ):
                violations.append(f"{module_name}:{node.lineno}")

    assert not violations, f"CLI progress must use public event fields: {violations}"


def test_cli_progress_does_not_reconstruct_internal_events() -> None:
    violations = []
    for module_name, tree in _consumer_trees().items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "types"
                and any(alias.name == "SimpleNamespace" for alias in node.names)
            ):
                violations.append(f"{module_name}:{node.lineno}")

    assert not violations, f"CLI progress must consume public events directly: {violations}"
