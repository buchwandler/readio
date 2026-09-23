from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import SimpleNamespace

from readio.api import PlanRequest


def test_python_api_example_executes_typed_plan_flow(capsys) -> None:
    calls: list[PlanRequest] = []

    class Plan:
        ok = True

        def to_dict(self) -> dict[str, object]:
            return {"ok": self.ok, "output": "example.wav"}

    class Speech:
        def plan(self, request: PlanRequest) -> Plan:
            calls.append(request)
            return Plan()

    app = SimpleNamespace(speech=Speech())
    example = Path(__file__).parents[1] / "examples" / "python_api.py"
    namespace = runpy.run_path(str(example))
    module_globals = namespace["plan_text"].__globals__
    module_globals["Readio"] = lambda: app
    assert module_globals["main"]() == 0
    output = json.loads(capsys.readouterr().out)

    assert output == {"ok": True, "output": "example.wav"}
    assert len(calls) == 1
    request = calls[0]
    assert request.operation == "render"
    assert request.input.document.text == "A no-TTS planning example."
    assert request.input.requested_format == "text"
    assert request.output.requested_path == Path("example.wav")
