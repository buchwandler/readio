from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest

CORE_PUBLIC_APIS = {
    "ssmdconvert": (
        "convert_book",
        "write_book_bundle",
        "load_book_workspace",
        "convert_document_artifact",
        "write_document_bundle",
        "load_document_workspace",
    ),
    "utterplan": (
        "compile_ssmdoc",
        "compile_book",
        "render_ssmdoc",
        "render_book",
    ),
    "voicerender": (
        "render_document",
        "render_book",
        "list_engines",
        "engine_status",
        "engine_options",
        "discover_targets",
        "list_models",
        "model_status",
        "install_model",
        "verify_model",
        "remove_model",
        "list_voices",
        "inspect_voice",
        "resolve_voice",
        "parse_voice_selector",
        "VoiceCloneRequest",
        "clone_voice_profile",
        "create_voice_source",
        "load_voice_source",
        "save_voice_source",
        "validate_voice_source",
        "create_render_profile",
        "load_render_profile",
        "save_render_profile",
        "validate_render_profile",
        "refresh_render_profile",
        "list_profile_roles",
        "set_profile_role",
        "remove_profile_role",
        "set_default_profile_role",
    ),
    "audiocompose": ("compose_document", "compose_book"),
}


@pytest.mark.parametrize(("package", "symbols"), CORE_PUBLIC_APIS.items())
def test_core_specialist_public_apis_are_importable(package: str, symbols: tuple[str, ...]) -> None:
    module: Any = import_module(package)

    missing = [symbol for symbol in symbols if not callable(getattr(module, symbol, None))]

    assert not missing, f"{package} is missing public root APIs: {', '.join(missing)}"


def test_optional_audioexport_public_apis_are_importable() -> None:
    module = pytest.importorskip("audioexport")

    missing = [
        symbol
        for symbol in ("export_document", "export_book")
        if not callable(getattr(module, symbol, None))
    ]

    assert not missing, f"audioexport is missing public root APIs: {', '.join(missing)}"
