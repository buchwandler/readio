from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from ssmdconvert import (
    Book as SSMDConvertBook,
)
from ssmdconvert import (
    BookBundleError as SSMDConvertBookBundleError,
)
from ssmdconvert import (
    BookBundleValidationError as SSMDConvertBookBundleValidationError,
)
from ssmdconvert import (
    BookChapter as SSMDConvertBookChapter,
)
from ssmdconvert import (
    BookError as SSMDConvertBookError,
)
from ssmdconvert import (
    BookInspectionChapter as SSMDConvertBookInspectionChapter,
)
from ssmdconvert import (
    ChapterSelectionError as SSMDConvertChapterSelectionError,
)
from ssmdconvert import (
    MissingDependencyError as SSMDConvertMissingDependencyError,
)
from ssmdconvert import (
    SourceInfo as SSMDConvertSourceInfo,
)
from ssmdconvert import (
    SSMDConvertError,
)
from ssmdconvert import (
    UnsupportedBookSourceError as SSMDConvertUnsupportedBookSourceError,
)
from ssmdconvert import (
    UnsupportedInputError as SSMDConvertUnsupportedInputError,
)
from ssmdconvert import __version__ as ssmdconvert_version
from ssmdconvert import (
    convert as ssmdconvert_convert,
)
from ssmdconvert import (
    convert_book as ssmdconvert_convert_book,
)
from ssmdconvert import (
    convert_content as ssmdconvert_convert_content,
)
from ssmdconvert import (
    inspect_book as ssmdconvert_inspect_book,
)
from ssmdconvert import (
    load_book_bundle as ssmdconvert_load_book_bundle,
)
from ssmdconvert import (
    write_book_bundle as ssmdconvert_write_book_bundle,
)

from ..errors import InputError
from ..jsonutil import JsonValue, json_value


class BookInputError(InputError):
    code = "input.book_conversion_failed"


class BookSelectionError(InputError):
    code = "input.book_selection_invalid"


class MissingInputDependencyError(InputError):
    code = "input.converter_dependency_missing"


@dataclass(frozen=True, slots=True)
class CanonicalDocument:
    source: Path | None
    source_format: str
    media_type: str | None
    source_name: str
    ssmd: str
    metadata: Mapping[str, JsonValue]
    converter_version: str


@dataclass(frozen=True, slots=True)
class CanonicalChapter:
    id: str
    source_number: int
    title: str
    ssmd: str
    source_id: str | None
    href: str | None
    source_parent_id: str | None
    parent_id: str | None
    level: int
    char_count: int | None
    diagnostics: tuple[Mapping[str, JsonValue], ...]


@dataclass(frozen=True, slots=True)
class CanonicalBook:
    source_format: str
    source_name: str
    media_type: str | None
    metadata: Mapping[str, JsonValue]
    chapters: tuple[CanonicalChapter, ...]
    source_sha256: str
    source_chapter_count: int | None
    converter_version: str


@dataclass(frozen=True, slots=True)
class BookInspectionChapter:
    number: int
    source_id: str | None
    title: str
    href: str | None
    source_parent_id: str | None
    parent_id: str | None
    level: int
    char_count: int | None
    diagnostics: tuple[Mapping[str, JsonValue], ...]


@dataclass(frozen=True, slots=True)
class BookInspection:
    source: Path
    metadata: Mapping[str, JsonValue]
    chapters: tuple[BookInspectionChapter, ...]
    source_format: str
    source_name: str
    media_type: str | None
    converter_version: str


def _json_mapping(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], json_value(dict(value)))


def _diagnostics(value: tuple[Mapping[str, Any], ...]) -> tuple[Mapping[str, JsonValue], ...]:
    return tuple(_json_mapping(item) for item in value)


def _canonical_chapter(chapter: SSMDConvertBookChapter) -> CanonicalChapter:
    return CanonicalChapter(
        id=chapter.id,
        source_number=chapter.source_number,
        title=chapter.title,
        ssmd=chapter.ssmd,
        source_id=chapter.source_id,
        href=chapter.href,
        source_parent_id=chapter.source_parent_id,
        parent_id=chapter.parent_id,
        level=chapter.level,
        char_count=chapter.char_count,
        diagnostics=_diagnostics(chapter.diagnostics),
    )


def _canonical_book(book: SSMDConvertBook) -> CanonicalBook:
    return CanonicalBook(
        source_format=book.source.format,
        source_name=book.source.name or "",
        media_type=book.source.media_type,
        metadata=_json_mapping(book.metadata),
        chapters=tuple(_canonical_chapter(chapter) for chapter in book.chapters),
        source_sha256=book.source_sha256,
        source_chapter_count=book.source_chapter_count,
        converter_version=ssmdconvert_version,
    )


def _canonical_inspection_chapter(
    chapter: SSMDConvertBookInspectionChapter,
) -> BookInspectionChapter:
    return BookInspectionChapter(
        number=chapter.source_number,
        source_id=chapter.source_id,
        title=chapter.title,
        href=chapter.href,
        source_parent_id=chapter.source_parent_id,
        parent_id=chapter.parent_id,
        level=chapter.level,
        char_count=chapter.char_count,
        diagnostics=_diagnostics(chapter.diagnostics),
    )


def _book_inspection(
    source: Path,
    source_format: str,
    source_name: str,
    media_type: str | None,
    metadata: Mapping[str, Any],
    chapters: tuple[SSMDConvertBookInspectionChapter, ...],
) -> BookInspection:
    return BookInspection(
        source=source,
        metadata=_json_mapping(metadata),
        chapters=tuple(_canonical_inspection_chapter(chapter) for chapter in chapters),
        source_format=source_format,
        source_name=source_name,
        media_type=media_type,
        converter_version=ssmdconvert_version,
    )


def _book_input_error(error: Exception, source: Path) -> InputError:
    if isinstance(error, SSMDConvertChapterSelectionError):
        return BookSelectionError(str(error), source_path=source)
    if isinstance(error, SSMDConvertBookBundleValidationError):
        return BookInputError(str(error), source_path=source, code="input.book_bundle_invalid")
    if isinstance(error, SSMDConvertBookBundleError):
        return BookInputError(str(error), source_path=source, code="input.book_bundle_failed")
    if isinstance(error, SSMDConvertMissingDependencyError):
        return MissingInputDependencyError(
            str(error), source_path=source, code="input.book_dependency_missing"
        )
    if isinstance(error, SSMDConvertUnsupportedBookSourceError):
        return BookInputError(
            str(error), source_path=source, code="input.book_format_unsupported"
        )
    if isinstance(error, SSMDConvertBookError):
        return BookInputError(str(error), source_path=source)
    return BookInputError(str(error), source_path=source)


def book_source_kind(source: Path) -> Literal["epub", "bundle"]:
    path = source.expanduser().resolve()
    name = path.name.casefold()
    if path.is_dir() and name.endswith(".ssmdbook"):
        return "bundle"
    if path.is_file():
        if name.endswith(".ssmdbook.zip"):
            return "bundle"
        if path.suffix.casefold() == ".epub":
            return "epub"
    if not path.exists():
        raise InputError(
            f"book source does not exist: {path}",
            source_path=path,
            code="input.not_found",
        )
    raise BookInputError(
        f"unsupported book source {path.name!r}; expected an EPUB or .ssmdbook bundle",
        source_path=path,
        code="input.book_format_unsupported",
    )


def convert_document_source(source: Path) -> CanonicalDocument:
    path = source.expanduser().resolve()
    is_book_bundle = (path.is_dir() and path.name.casefold().endswith(".ssmdbook")) or (
        path.is_file() and path.name.casefold().endswith(".ssmdbook.zip")
    )
    if is_book_bundle:
        raise InputError(
            "book bundles are multi-chapter inputs; use `readio audiobook init`",
            source_path=path,
            code="input.book_bundle_requires_audiobook",
        )
    if not path.is_file():
        raise InputError(
            f"document source is not a file: {path}",
            source_path=path,
            code="input.not_found",
        )

    try:
        result = ssmdconvert_convert(path)
    except SSMDConvertUnsupportedInputError as error:
        raise InputError(
            f"unsupported document input: {path.name}",
            source_path=path,
            code="input.format_unsupported",
        ) from error
    except SSMDConvertMissingDependencyError as error:
        raise MissingInputDependencyError(
            str(error), source_path=path, code="input.converter_dependency_missing"
        ) from error
    except SSMDConvertError as error:
        raise InputError(
            f"could not convert {path.name}: {error}",
            source_path=path,
            code="input.conversion_failed",
        ) from error
    except (OSError, UnicodeError, ValueError) as error:
        raise InputError(
            f"could not convert {path.name}: {error}",
            source_path=path,
            code="input.conversion_failed",
        ) from error

    source_info = result.document.source
    return CanonicalDocument(
        source=path,
        source_format=source_info.format,
        media_type=source_info.media_type,
        source_name=source_info.name or path.name,
        ssmd=result.ssmd,
        metadata=_json_mapping(result.document.metadata),
        converter_version=ssmdconvert_version,
    )



def convert_document_content(
    content: str,
    *,
    input_format: Literal["text", "markdown", "html"],
    source_name: str = "<memory>",
    source_path: Path | None = None,
    title: str | None = None,
    language: str | None = None,
) -> CanonicalDocument:
    try:
        result = ssmdconvert_convert_content(
            content,
            input_format=input_format,
            source_name=source_name,
            title=title,
            language=language,
        )
    except SSMDConvertUnsupportedInputError as error:
        raise InputError(
            f"unsupported document content format: {input_format}",
            source_path=source_path,
            code="input.format_unsupported",
        ) from error
    except SSMDConvertMissingDependencyError as error:
        raise MissingInputDependencyError(
            str(error), source_path=source_path, code="input.converter_dependency_missing"
        ) from error
    except SSMDConvertError as error:
        raise InputError(
            f"could not convert {source_name}: {error}",
            source_path=source_path,
            code="input.conversion_failed",
        ) from error
    except (OSError, UnicodeError, ValueError) as error:
        raise InputError(
            f"could not convert {source_name}: {error}",
            source_path=source_path,
            code="input.conversion_failed",
        ) from error

    source_info = result.document.source
    return CanonicalDocument(
        source=source_path,
        source_format=source_info.format,
        media_type=source_info.media_type,
        source_name=source_info.name or source_name,
        ssmd=result.ssmd,
        metadata=_json_mapping(result.document.metadata),
        converter_version=ssmdconvert_version,
    )

def inspect_book_source(source: Path) -> BookInspection:
    path = source.expanduser().resolve()
    kind = book_source_kind(path)
    try:
        if kind == "epub":
            inspection = ssmdconvert_inspect_book(path)
            source_info = inspection.source
            return _book_inspection(
                path,
                source_info.format,
                source_info.name or path.name,
                source_info.media_type,
                inspection.metadata,
                inspection.chapters,
            )
        book = ssmdconvert_load_book_bundle(path)
    except (SSMDConvertError, OSError, UnicodeError, ValueError) as error:
        raise _book_input_error(error, path) from error
    return BookInspection(
        source=path,
        metadata=_json_mapping(book.metadata),
        chapters=tuple(
            BookInspectionChapter(
                number=chapter.source_number,
                source_id=chapter.source_id,
                title=chapter.title,
                href=chapter.href,
                source_parent_id=chapter.source_parent_id,
                parent_id=chapter.parent_id,
                level=chapter.level,
                char_count=chapter.char_count,
                diagnostics=_diagnostics(chapter.diagnostics),
            )
            for chapter in book.chapters
        ),
        source_format=book.source.format,
        source_name=book.source.name or path.name,
        media_type=book.source.media_type,
        converter_version=ssmdconvert_version,
    )


def convert_book_source(source: Path, *, chapters: str | None = "all") -> CanonicalBook:
    path = source.expanduser().resolve()
    if book_source_kind(path) != "epub":
        raise BookInputError(
            "book conversion requires an EPUB source",
            source_path=path,
            code="input.book_format_unsupported",
        )
    try:
        book = ssmdconvert_convert_book(path, chapters=chapters)
    except (SSMDConvertError, OSError, UnicodeError, ValueError) as error:
        raise _book_input_error(error, path) from error
    return _canonical_book(book)


def load_book_bundle_source(source: Path) -> CanonicalBook:
    path = source.expanduser().resolve()
    if book_source_kind(path) != "bundle":
        raise BookInputError(
            "book bundle loading requires a .ssmdbook source",
            source_path=path,
            code="input.book_format_unsupported",
        )
    try:
        book = ssmdconvert_load_book_bundle(path)
    except (SSMDConvertError, OSError, UnicodeError, ValueError) as error:
        raise _book_input_error(error, path) from error
    return _canonical_book(book)


def write_book_bundle(
    book: CanonicalBook,
    output: Path,
    *,
    format: Literal["directory", "zip"],
) -> Path:
    ssmdconvert_book = SSMDConvertBook(
        source=SSMDConvertSourceInfo(
            format=book.source_format,
            media_type=book.media_type,
            name=book.source_name,
        ),
        metadata=dict(book.metadata),
        chapters=tuple(
            SSMDConvertBookChapter(
                id=chapter.id,
                source_number=chapter.source_number,
                title=chapter.title,
                ssmd=chapter.ssmd,
                source_id=chapter.source_id,
                href=chapter.href,
                source_parent_id=chapter.source_parent_id,
                parent_id=chapter.parent_id,
                level=chapter.level,
                char_count=chapter.char_count,
                diagnostics=tuple(dict(item) for item in chapter.diagnostics),
            )
            for chapter in book.chapters
        ),
        source_sha256=book.source_sha256,
        source_chapter_count=book.source_chapter_count,
    )
    try:
        return ssmdconvert_write_book_bundle(ssmdconvert_book, output, format=format)
    except (SSMDConvertError, OSError, UnicodeError, ValueError) as error:
        raise _book_input_error(error, output) from error


__all__ = [
    "BookInputError",
    "BookInspection",
    "BookInspectionChapter",
    "BookSelectionError",
    "CanonicalBook",
    "CanonicalChapter",
    "CanonicalDocument",
    "MissingInputDependencyError",
    "book_source_kind",
    "convert_book_source",
    "convert_document_content",
    "convert_document_source",
    "inspect_book_source",
    "load_book_bundle_source",
    "write_book_bundle",
]
