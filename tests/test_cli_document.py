from __future__ import annotations

import json
from pathlib import Path

from readio import cli, document


def test_render_cli_settings_precedence_and_json_progress_separation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config = tmp_path / "readio.toml"
    config.write_text(
        """format = "readio-settings"
schema_version = 1

[document]
language = "fr-FR"

[document.plan]
text_preparation = "identity"

[document.plan.linguistics]
use_spacy = true

[render]
force = false

[compose]
sample_rate = 32000
channels = 1
target_lufs = -20.0

[export]
format = "flac"
bitrate = "128k"
sample_rate = 44100
channels = 1
""",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}
    result = document.DocumentRenderResult(
        artifacts_dir=tmp_path / "artifacts",
        conversion_result=object(),
        render_result=object(),
        output=tmp_path / "story.mp3",
    )

    def render(request: document.DocumentRenderRequest, *, on_progress=None):
        captured["request"] = request
        captured["callback"] = on_progress
        if on_progress is not None:
            on_progress(
                document.ProgressEvent("plan", "progress", current=2, total=3, message="planning")
            )
        return result

    monkeypatch.setattr(cli.api, "render_document", render)
    source = tmp_path / "story.txt"
    profile = tmp_path / "voice.renderprofile.toml"

    assert (
        cli.main(
            [
                "render",
                str(source),
                "--profile",
                str(profile),
                "--config",
                str(config),
                "--language",
                "de-DE",
                "--spacy",
                "off",
                "--target-lufs",
                "-17",
                "--export-sample-rate",
                "22050",
                "--format",
                "mp3",
                "--force",
                "--json",
            ]
        )
        == 0
    )

    request = captured["request"]
    assert isinstance(request, document.DocumentRenderRequest)
    assert request.conversion_options is not None
    assert request.conversion_options.language == "de-DE"
    assert request.planner_config is not None
    assert request.planner_config.language == "de-DE"
    assert request.planner_config.text_preparation == "identity"
    assert request.planner_config.linguistics.use_spacy is False
    assert request.compose_output_policy is not None
    assert request.compose_output_policy.sample_rate == 32000
    assert request.compose_output_policy.loudness.target_lufs == -17.0
    assert request.export_sample_rate == 22050
    assert request.export_format == "mp3"
    assert request.export_bitrate == "128k"
    assert request.force is True
    assert captured["callback"] is not None

    captured_output = capsys.readouterr()
    assert json.loads(captured_output.out) == {
        "ok": True,
        "result": {
            "artifacts_dir": str(result.artifacts_dir),
            "completed_stages": ["convert", "synth"],
            "stop_after": None,
            "ssmd": None,
            "utterplan": None,
            "renderplan": None,
            "preflight": None,
            "render_dir": None,
            "composed_wav": None,
            "output": str(result.output),
            "pending_composition": False,
        },
    }
    assert "plan: progress 2/3 planning" in captured_output.err
