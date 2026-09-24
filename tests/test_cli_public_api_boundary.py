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
CLI_MODULES = ("readio.cli", "readio.spotify_cli")
ROOT = Path(__file__).resolve().parents[1]


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
    path = ROOT.joinpath(*module_name.split(".")).with_suffix(".py")
    imported = _imported_modules(path.read_text(encoding="utf-8"), module_name)
    return {
        name
        for name in imported
        if name.startswith("readio.") and name.split(".")[1] in FORBIDDEN_MODULES
    }


def test_cli_modules_import_application_operations_from_public_api() -> None:
    violations = {
        module_name: sorted(_forbidden_imports(module_name))
        for module_name in CLI_MODULES
        if _forbidden_imports(module_name)
    }

    assert not violations, (
        "CLI modules must consume domain operations through readio.api; "
        f"direct implementation imports found: {violations}"
    )
