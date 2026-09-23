from __future__ import annotations

from pathlib import Path

import pytest

from readio.api import (
    DiscoveryError,
    ExecutionError,
    InputError,
    IntegrationError,
    InvalidRequestError,
    OutputError,
    ProjectConflictError,
    ProjectError,
    ProjectFormatError,
    ProjectNotFoundError,
    ReadioError,
    ResolutionError,
    error_boundary,
    translate_exception,
)


@pytest.mark.parametrize(
    "error_type",
    [
        DiscoveryError,
        ExecutionError,
        InputError,
        IntegrationError,
        InvalidRequestError,
        OutputError,
        ProjectConflictError,
        ProjectError,
        ProjectFormatError,
        ProjectNotFoundError,
        ResolutionError,
    ],
)
def test_public_exceptions_share_readio_error_contract(error_type: type[ReadioError]) -> None:
    error = error_type("failure", details={"retryable": False})
    assert isinstance(error, ReadioError)
    assert error.code == error_type.code
    assert error.source_path is None
    assert error.details == {"retryable": False}


def test_translation_assigns_stable_codes_and_source_path(tmp_path: Path) -> None:
    source = tmp_path / "missing.txt"
    translated = translate_exception(FileNotFoundError(2, "missing", str(source)))

    assert isinstance(translated, InputError)
    assert translated.code == "input.not_found"
    assert translated.source_path == source
    assert translated.details == {"exception_type": "FileNotFoundError"}

    conflict = translate_exception(FileExistsError(str(source)))
    assert isinstance(conflict, OutputError)
    assert conflict.code == "output.exists"


def test_error_boundary_translates_builtins_and_preserves_readio_errors() -> None:
    with pytest.raises(InvalidRequestError) as invalid, error_boundary():
        raise ValueError("invalid option")
    assert invalid.value.code == "request.invalid"

    original = ProjectNotFoundError("missing project")
    with pytest.raises(ProjectNotFoundError) as raised, error_boundary():
        raise original
    assert raised.value is original
