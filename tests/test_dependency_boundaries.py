from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_MODULES = frozenset(
    {
        "utterplan",
        "ssmd",
        "ssmdconvert",
        "ttsready",
        "onnxvoice",
        "audiocompose",
        "pykokoro",
        "pipersynth",
        "pocketsynth",
        "kittensynth",
    }
)

# Initial inventory from the source tree. Tighten this map as each integration is
# consolidated, and review any new contact before changing the baseline.
BASELINE_IMPORTERS = {
    "audiocompose": {
        "readio/stages/composition.py",
    },
    "kittensynth": {"readio/engines/kittensynth.py"},
    "pipersynth": {"readio/engines/pipersynth.py"},
    "pocketsynth": {"readio/engines/pocketsynth.py"},
    "pykokoro": {"readio/engines/pykokoro.py"},
    "ssmd": {
        "readio/ssmd.py",
    },
    "ssmdconvert": {"readio/integrations/ssmdconvert.py"},
    "utterplan": {
        "readio/planning/compiler.py",
        "readio/planning/policy.py",
        "readio/planning/semantic.py",
        "readio/rendering/lowering.py",
    },
}

API_IMPORT_BASELINE = {}


def _collect_domain_imports() -> tuple[dict[str, set[str]], dict[str, int]]:
    importers: dict[str, set[str]] = defaultdict(set)
    imported_names: dict[str, int] = defaultdict(int)
    for path in (REPO_ROOT / "readio").rglob("*.py"):
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [(alias.name.split(".", 1)[0], 1) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports = [(node.module.split(".", 1)[0], len(node.names))]
            else:
                continue
            for module, name_count in imports:
                if module in DOMAIN_MODULES:
                    importers[module].add(relative_path)
                    imported_names[module] += name_count
    return dict(importers), dict(imported_names)


def _api_importers(importers: dict[str, set[str]]) -> dict[str, set[str]]:
    return {
        module: {path for path in paths if path.startswith("readio/api/")}
        for module, paths in importers.items()
        if any(path.startswith("readio/api/") for path in paths)
    }


def test_domain_importers_match_current_baseline() -> None:
    importers, _ = _collect_domain_imports()
    assert importers == BASELINE_IMPORTERS, (
        "External domain import surface changed. Review the new contact and update "
        "the baseline only as part of an approved boundary change. "
        f"Expected {BASELINE_IMPORTERS!r}, found {importers!r}."
    )


def test_public_api_domain_imports_match_current_baseline() -> None:
    importers, _ = _collect_domain_imports()
    assert _api_importers(importers) == API_IMPORT_BASELINE


def test_contact_surface_metrics_match_source_inventory() -> None:
    importers, imported_names = _collect_domain_imports()
    importing_files = set().union(*importers.values())
    metrics = {
        "domain_packages": len(importers),
        "importing_files": len(importing_files),
        "package_file_contacts": sum(len(paths) for paths in importers.values()),
        "imported_names": sum(imported_names.values()),
    }
    print(f"Readio external domain import baseline: {metrics}")
    assert metrics == {
        "domain_packages": 8,
        "importing_files": 11,
        "package_file_contacts": 11,
        "imported_names": 70,
    }
