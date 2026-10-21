"""Convert and plan an SSMD bundle through Readio's Python API."""

from __future__ import annotations

import argparse
from pathlib import Path

from readio.api import PlanRequest, Readio, RenderPlanRequest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--language", default="en-US")
    parser.add_argument("--chapters", default="all")
    args = parser.parse_args()

    service = Readio()
    workspace = service.create_workspace(args.source)
    compiled = service.plan(
        workspace,
        PlanRequest(language=args.language, chapters=args.chapters),
    )
    projection = service.render_plan(
        workspace,
        RenderPlanRequest(
            profile=args.profile,
            chapters=args.chapters,
        ),
    )
    print(
        f"Bundle: {workspace.root} (kind: {workspace.kind}); "
        f"compiled: {compiled.output_dir}; variant: {projection.variant}"
    )


if __name__ == "__main__":
    main()
