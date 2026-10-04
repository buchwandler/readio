from __future__ import annotations

import os
from importlib.metadata import version
from pathlib import Path

import pytest
import ssmdconvert
from ssmdconvert import (
    Book,
    BookChapter,
    BookInspectionChapter,
    ChapterSelectionError,
    MissingDependencyError,
    SSMDConvertError,
    UnsupportedBookSourceError,
    UnsupportedInputError,
    convert,
    convert_book,
    convert_content,
    inspect_book,
    load_book_bundle,
    write_book_bundle,
)


def test_readio_public_ssmdconvert_contract_is_available_in_supported_release_family() -> None:
    assert version("ssmdconvert").startswith("0.1.")
    assert ssmdconvert.__version__ == version("ssmdconvert")
    assert all(
        callable(operation)
        for operation in (
            convert,
            convert_book,
            convert_content,
            inspect_book,
            load_book_bundle,
            write_book_bundle,
        )
    )
    assert all(
        isinstance(value, type)
        for value in (
            Book,
            BookChapter,
            BookInspectionChapter,
            ChapterSelectionError,
            MissingDependencyError,
            SSMDConvertError,
            UnsupportedBookSourceError,
            UnsupportedInputError,
        )
    )


def test_readio_keeps_parser_and_private_conversion_imports_outside_its_boundary() -> None:
    source_root = Path(__file__).parents[1] / "readio"
    forbidden_prefixes = (
        "from ssmdconvert.",
        "import ssmdconvert.",
        "from epub2text",
        "import epub2text",
        "from ebooklib",
        "import ebooklib",
        "from pypdf",
        "import pypdf",
        "from docx",
        "import docx",
    )
    for source in source_root.rglob("*.py"):
        for line in source.read_text(encoding="utf-8").splitlines():
            assert not line.strip().startswith(forbidden_prefixes), source


def test_minimum_ssmdconvert_release_contract_when_requested() -> None:
    if os.environ.get("READIO_TEST_SSMD_CONVERT_MINIMUM") != "1":
        pytest.skip("run this assertion in the minimum-release contract job")
    assert version("ssmdconvert") == "0.1.2"


def test_minimum_utterplan_release_contract_when_requested() -> None:
    if os.environ.get("READIO_TEST_UTTERPLAN_MINIMUM") != "1":
        pytest.skip("run this assertion in the minimum-release contract job")
    assert version("utterplan") == "0.3.4"
