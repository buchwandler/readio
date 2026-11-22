"""MCP tools implemented only through Readio's public gateway contract."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]

from readio.api import (
    Readio,
    ReadioError,
    RebuildStage,
    RenderRequest,
    SpeakRequest,
    WorkflowResolutionError,
    describe_error,
    error_info_to_dict,
    serialize_public,
)

mcp = FastMCP("Readio")


def _serialized(operation: Callable[[], Any]) -> Any:
    try:
        return serialize_public(operation())
    except ReadioError as error:
        return error_info_to_dict(describe_error(error))


def _explicit_path(value: str, name: str) -> Path:
    if not value.strip():
        raise WorkflowResolutionError(f"{name} must be an explicit non-empty path")
    return Path(value).expanduser()


def _render_request(
    source: str,
    profile: str,
    output: str,
    *,
    chapters: str = "all",
    language: str | None = None,
    bundle: str | None = None,
    variant: str | None = None,
    output_format: str | None = None,
    rebuild_from: RebuildStage | None = None,
    replace_output: bool = False,
) -> RenderRequest:
    return RenderRequest(
        source=_explicit_path(source, "source"),
        profile=_explicit_path(profile, "profile"),
        output=_explicit_path(output, "output"),
        chapters=chapters,
        language=language,
        bundle=_explicit_path(bundle, "bundle") if bundle is not None else None,
        variant=variant,
        format=output_format,
        rebuild_from=rebuild_from,
        replace_output=replace_output,
    )


@mcp.tool()
def speech_inspect_source(source: str) -> dict[str, object]:
    """Inspect one explicit source path without creating a workspace."""
    return _serialized(lambda: Readio().source_capabilities(_explicit_path(source, "source")))


@mcp.tool()
def speech_inspect_workflow(
    source: str,
    profile: str,
    output: str,
    chapters: str = "all",
    language: str | None = None,
    bundle: str | None = None,
    variant: str | None = None,
    output_format: str | None = None,
    rebuild_from: RebuildStage | None = None,
    replace_output: bool = False,
) -> dict[str, object]:
    """Inspect a workflow with explicit source, profile, and output paths."""
    return _serialized(
        lambda: Readio().inspect(
            _render_request(
                source,
                profile,
                output,
                chapters=chapters,
                language=language,
                bundle=bundle,
                variant=variant,
                output_format=output_format,
                rebuild_from=rebuild_from,
                replace_output=replace_output,
            )
        )
    )


@mcp.tool()
def speech_capabilities() -> dict[str, object]:
    """Return read-only aggregate Readio and specialist capabilities."""
    return _serialized(lambda: Readio().capabilities())


@mcp.tool()
def speech_list_engines() -> dict[str, object] | list[dict[str, object]]:
    """List synthesis engines using Readio's normalized catalog records."""
    return _serialized(lambda: Readio().list_engines())


@mcp.tool()
def speech_list_voices(
    engine: str,
    target: str,
    language: str | None = None,
) -> dict[str, object] | list[dict[str, object]]:
    """List cached voices for an explicit engine and target without network refresh."""
    return _serialized(
        lambda: Readio().list_voices(engine=engine, target=target, language=language, offline=True)
    )


@mcp.tool()
def speech_render(
    source: str,
    profile: str,
    output: str,
    chapters: str = "all",
    language: str | None = None,
    bundle: str | None = None,
    variant: str | None = None,
    output_format: str | None = None,
    rebuild_from: RebuildStage | None = None,
    replace_output: bool = False,
    include_report: bool = False,
) -> dict[str, object]:
    """Render only when the canonical inspected plan is executable and authorized."""

    def execute() -> Any:
        service = Readio()
        request = _render_request(
            source,
            profile,
            output,
            chapters=chapters,
            language=language,
            bundle=bundle,
            variant=variant,
            output_format=output_format,
            rebuild_from=rebuild_from,
            replace_output=replace_output,
        )
        plan = service.inspect(request)
        if not plan.executable:
            return plan
        return service.render(request, include_report=include_report)

    return _serialized(execute)


@mcp.tool()
def speech_speak(
    text: str,
    engine: str,
    target: str,
    language: str | None = None,
    speaker: str | int | None = None,
) -> dict[str, object]:
    """Synthesize explicit text and return WAV audio as base64 without playback."""
    return _serialized(
        lambda: Readio().synthesize(
            SpeakRequest(
                text=text,
                engine=engine,
                target=target,
                language=language,
                speaker=speaker,
            )
        )
    )


def main() -> None:
    """Run the optional Readio MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
