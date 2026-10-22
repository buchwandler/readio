from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOTS = frozenset(
    {
        "pykokoro",
        "pipersynth",
        "pocketsynth",
        "kittensynth",
        "supertonicsynth",
        "inflectsynth",
        "soprosynth",
        "qwen3synth",
        "onnxvoice",
        "onnxruntime",
        "voicefolio",
    }
)
SPECIALIST_ROOTS = frozenset(
    {"ssmdconvert", "utterplan", "voicerender", "audiocompose", "audioexport"}
)
PRIVATE_IMPORT_PREFIXES = (
    "voicerender.engines",
    "audiocompose.integrations",
    "audioexport.fftools",
    "utterplan.renderplan_",
)
LEGACY_IDENTIFIERS = (
    '".readio"',
    '"project.json"',
    '"readio.plan.v2"',
    '"readio.render-manifest"',
    '"v03_to_v04"',
)


def _runtime_imports() -> set[str]:
    imports: set[str] = set()
    for path in (ROOT / "readio").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports.add(node.module)
    return imports


def test_runtime_does_not_import_backend_implementations() -> None:
    roots = {module.split(".", 1)[0] for module in _runtime_imports()}
    assert not roots & BACKEND_ROOTS


def test_runtime_imports_only_specialist_package_roots() -> None:
    private_imports = {
        module
        for module in _runtime_imports()
        if any(module.startswith(prefix) for prefix in PRIVATE_IMPORT_PREFIXES)
    }
    assert not private_imports


def test_runtime_facade_imports_only_voicerender_from_the_synthesis_stack() -> None:
    runtime_path = ROOT / "readio/runtime.py"
    tree = ast.parse(runtime_path.read_text(encoding="utf-8"), filename=str(runtime_path))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert imported & SPECIALIST_ROOTS == {"voicerender"}
    assert not imported & {"onnxvoice", "onnxruntime"}


def test_specialist_imports_use_only_public_roots() -> None:
    specialist_imports = {
        module for module in _runtime_imports() if module.split(".", 1)[0] in SPECIALIST_ROOTS
    }
    assert specialist_imports <= SPECIALIST_ROOTS


def test_runtime_contains_no_legacy_persistence_identifiers() -> None:
    for path in (ROOT / "readio").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for identifier in LEGACY_IDENTIFIERS:
            assert identifier not in source, f"{path.relative_to(ROOT)} contains {identifier}"


def test_legacy_project_modules_are_absent() -> None:
    removed_modules = (
        "readio/book.py",
        "readio/document.py",
        "readio/cli_document.py",
        "readio/config.py",
        "readio/cli_config.py",
        "readio/api/book.py",
        "readio/api/document.py",
        "readio/api/configuration.py",
    )
    for module in removed_modules:
        assert not (ROOT / module).exists(), f"legacy module was restored: {module}"
