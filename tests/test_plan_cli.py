"""Tests for readio.plan CLI integration."""

from __future__ import annotations

import json

import pytest

from readio.cli import build_parser


class TestPlanCommand:
    """Test the `readio plan` CLI command."""

    def test_plan_help(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["plan", "--help"])
        assert exc.value.code == 0

    def test_plan_text_input(self, capsys) -> None:
        parser = build_parser()
        args = parser.parse_args(["plan", "Hello world"])
        code = args.func(args)
        assert code == 0
        output = capsys.readouterr().out
        assert "Synthesis" in output
        assert "No TTS model was loaded" in output

    def test_plan_json_output(self, capsys) -> None:
        parser = build_parser()
        args = parser.parse_args(["plan", "Hello world", "--json"])
        code = args.func(args)
        assert code == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["schema"] == "readio.plan.v1"
        assert data["ok"] is True
        assert "synthesis" in data

    def test_plan_with_model(self, capsys) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "plan",
                "Hello world",
                "--lang",
                "de",
                "--model",
                "de-thorsten",
                "--model-source",
                "github",
                "--json",
            ]
        )
        code = args.func(args)
        assert code == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["synthesis"]["model"]["id"] == "de-thorsten"

    def test_plan_with_format(self, capsys) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "plan",
                "Hello world",
                "--format",
                "mp3",
                "--json",
            ]
        )
        code = args.func(args)
        assert code == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["output"]["format"] == "mp3"


class TestRenderDryRun:
    """Test `readio render --dry-run`."""

    def test_render_dry_run_text(self, capsys) -> None:
        parser = build_parser()
        args = parser.parse_args(["render", "Hello world", "--dry-run"])
        code = args.func(args)
        assert code == 0
        output = capsys.readouterr().out
        assert "Synthesis" in output
        assert "No TTS model was loaded" in output

    def test_render_dry_run_json(self, capsys) -> None:
        parser = build_parser()
        args = parser.parse_args(["render", "Hello world", "--dry-run", "--json"])
        code = args.func(args)
        assert code == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["schema"] == "readio.plan.v1"
        assert data["ok"] is True

    def test_render_dry_run_with_model(self, capsys) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "render",
                "Hello world",
                "--lang",
                "de",
                "--model",
                "de-thorsten",
                "--model-source",
                "github",
                "--dry-run",
                "--json",
            ]
        )
        code = args.func(args)
        assert code == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["synthesis"]["model"]["id"] == "de-thorsten"


class TestPlanDryRunEquivalence:
    """Test that `readio plan` and `readio render --dry-run` produce equivalent results."""

    def test_plan_and_render_dry_run_match(self, capsys) -> None:
        """For the same request, plan and render --dry-run should produce the same schema."""
        parser = build_parser()

        # Run plan
        plan_args = parser.parse_args(
            [
                "plan",
                "Hello world",
                "--lang",
                "de",
                "--model",
                "de-thorsten",
                "--model-source",
                "github",
                "--json",
            ]
        )
        plan_args.func(plan_args)
        plan_output = capsys.readouterr().out
        plan_data = json.loads(plan_output)

        # Run render --dry-run
        render_args = parser.parse_args(
            [
                "render",
                "Hello world",
                "--lang",
                "de",
                "--model",
                "de-thorsten",
                "--model-source",
                "github",
                "--dry-run",
                "--json",
            ]
        )
        render_args.func(render_args)
        render_output = capsys.readouterr().out
        render_data = json.loads(render_output)

        # Schema should match
        assert plan_data["schema"] == render_data["schema"]
        assert plan_data["ok"] == render_data["ok"]

        # Synthesis should match
        assert plan_data["synthesis"]["model"]["id"] == render_data["synthesis"]["model"]["id"]
        assert plan_data["synthesis"]["language"] == render_data["synthesis"]["language"]


class TestResolveVoicesRejection:
    """Planning must stay deterministic: --resolve-voices is rejected."""

    def test_plan_rejects_resolve_voices(self, tmp_path) -> None:
        source = tmp_path / "cast.ssmd"
        source.write_text('<div voice="host">Hello.</div>', encoding="utf-8")
        parser = build_parser()
        args = parser.parse_args(["plan", str(source), "--resolve-voices"])
        with pytest.raises(ValueError, match="not available during plan/dry-run"):
            args.func(args)

    def test_render_dry_run_rejects_resolve_voices(self, tmp_path) -> None:
        source = tmp_path / "cast.ssmd"
        source.write_text('<div voice="host">Hello.</div>', encoding="utf-8")
        parser = build_parser()
        args = parser.parse_args(["render", str(source), "--dry-run", "--resolve-voices"])
        with pytest.raises(ValueError, match="not available during plan/dry-run"):
            args.func(args)

    def test_rejection_mentions_deterministic_remediation(self, tmp_path) -> None:
        source = tmp_path / "cast.ssmd"
        source.write_text('<div voice="host">Hello.</div>', encoding="utf-8")
        parser = build_parser()
        args = parser.parse_args(["plan", str(source), "--resolve-voices"])
        with pytest.raises(ValueError, match="--voice-bind ROLE=VOICE_ID"):
            args.func(args)


class TestPlanForceFlag:
    def test_plan_parser_accepts_force(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["plan", "Hello world", "--force"])
        assert args.force is True

    def test_plan_force_flows_into_output_request(self, tmp_path, capsys) -> None:
        existing = tmp_path / "episode.wav"
        existing.write_bytes(b"x")
        parser = build_parser()
        args = parser.parse_args(["plan", "Hello world", "-o", str(existing), "--force", "--json"])
        code = args.func(args)
        assert code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["output"]["force"] is True
        assert all(d["code"] != "output_exists" for d in data["diagnostics"])

    def test_plan_without_force_reports_existing_output(self, tmp_path, capsys) -> None:
        existing = tmp_path / "episode.wav"
        existing.write_bytes(b"x")
        parser = build_parser()
        args = parser.parse_args(["plan", "Hello world", "-o", str(existing), "--json"])
        code = args.func(args)
        assert code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["output"]["force"] is False
        assert any(d["code"] == "output_exists" for d in data["diagnostics"])
