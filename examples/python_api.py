"""Inspect a complete Readio file-to-audio workflow without executing it."""

from __future__ import annotations

import argparse
from pathlib import Path

from readio.api import Readio, RenderRequest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--language", default="en-US")
    parser.add_argument("--chapters", default="all")
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()

    request = RenderRequest(
        source=args.source,
        profile=args.profile,
        language=args.language,
        chapters=args.chapters,
        bundle=args.bundle,
    )
    plan = Readio().inspect(request)
    print(f"Workspace mode: {plan.workspace_mode}")
    for step in plan.steps:
        print(f"{step.stage}: {step.action} ({step.state}) — {step.reason}")


if __name__ == "__main__":
    main()
