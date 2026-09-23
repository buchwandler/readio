"""Plan literal text through Readio's public Python API without loading TTS."""

from __future__ import annotations

import json
from pathlib import Path

from readio.api import (
    InputRequest,
    OutputRequest,
    PlanRequest,
    Readio,
    ResolvedPlan,
    SynthesisRequest,
    document_from_text,
)


def plan_text(app: Readio, text: str, output: Path) -> ResolvedPlan:
    request = PlanRequest(
        operation="render",
        input=InputRequest(
            document=document_from_text(text),
            requested_format="text",
        ),
        synthesis=SynthesisRequest(),
        output=OutputRequest(
            requested_format="wav",
            requested_path=output,
        ),
    )
    return app.speech.plan(request)


def main() -> int:
    plan = plan_text(Readio(), "A no-TTS planning example.", Path("example.wav"))
    print(json.dumps(plan.to_dict(), ensure_ascii=False))
    return 0 if plan.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
