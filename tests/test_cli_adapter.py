from __future__ import annotations

from readio import cli_adapter
from readio.api import SSMDAnalysis, SSMDVoiceReference, default_config


def test_prompt_missing_voices_skips_existing_bindings(monkeypatch, capsys) -> None:
    analysis = SSMDAnalysis(
        provider="kokoro",
        source_path=None,
        document_bindings={},
        default_bindings={},
        runtime_bindings={},
        voice_references=(
            SSMDVoiceReference(reference="host", count=2, lines=(1, 2)),
            SSMDVoiceReference(reference="guest", count=1, lines=(3,)),
        ),
        unresolved_references=("host", "guest"),
        diagnostics=(),
    )
    prompts = []
    monkeypatch.setattr(
        cli_adapter, "input", lambda prompt: prompts.append(prompt) or "1", raising=False
    )

    bindings = cli_adapter.prompt_missing_voices(
        analysis,
        default_config(),
        bindings={"host": "af_heart"},
    )

    assert bindings == {"guest": default_config().voices["kokoro"].ids[0]}
    assert prompts == ["Voice for guest [enter number or voice ID]: "]
    assert "SSMD uses 1 unconfigured voice references" in capsys.readouterr().out
