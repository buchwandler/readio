"""Tests for the project plan and role-binding CLI."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from utterplan import preflight_renderability

from readio import cli
from readio.api import ProjectRef, ProjectRoleMutationResult
from readio.api.projects import ProjectService
from readio.api.roles import RoleService
from readio.cli import build_parser
from readio.config import ReaderSettings, ReadioConfig
from readio.project import init_project
from readio.role_targets import VoiceTarget
from readio.stages.planning import load_primary_scope_plan


def test_plan_without_subcommand_builds_current_project(tmp_path, monkeypatch, capsys) -> None:
    requested = []
    result = SimpleNamespace(
        scopes=(),
        to_dict=lambda: {"project": str(tmp_path), "scopes": []},
    )
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())
    monkeypatch.setattr(
        ProjectService,
        "plan",
        lambda self, project, *, on_event=None: requested.append(project) or result,
    )

    args = build_parser().parse_args(["plan", "--json"])
    assert args.func(args) == 0

    result = json.loads(capsys.readouterr().out)
    assert requested == [Path.cwd()]
    assert result["project"] == str(tmp_path)
    assert result["scopes"] == []


def test_plan_help_is_project_scoped(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["plan", "--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "{build,inspect,repair,roles,bind,unbind}" in output
    for option in ("--engine", "--voice", "--model", "--format", "--output", "--voice-bind"):
        assert option not in output


def test_plan_group_exposes_only_project_subcommands() -> None:
    parser = build_parser()
    args = parser.parse_args(["plan", "roles", "--engine", "kokoro", "--json"])
    assert args.plan_action == "roles"
    assert args.engine == "kokoro"
    with pytest.raises(SystemExit):
        parser.parse_args(["plan", "roles", "--provider", "kokoro"])
    with pytest.raises(SystemExit):
        parser.parse_args(["plan", "Hello world"])
    with pytest.raises(SystemExit):
        parser.parse_args(["plan", "build", "--engine", "kokoro"])


def test_plan_roles_json_reports_project_roles(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "episode.ssmd"
    source.write_text(
        '---\nssmd_version: "0.9"\n---\n:::{voice="narrator"}\nHello.\n:::\n',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "episode.readio")
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())

    args = build_parser().parse_args(["plan", "roles", str(project.root), "--json"])
    assert args.func(args) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["project"] == str(project.root)
    assert result["engine"] == "kokoro"
    assert result["roles"][0]["role"] == "narrator"
    assert result["roles"][0]["origin"] == "config.voice_role"
    assert result["roles"][0]["effective_target"]["engine"] == "kokoro"
    assert "provider" not in result["roles"][0]["effective_target"]


def test_plan_roles_human_output_has_unresolved_guidance(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "episode.ssmd"
    source.write_text(
        '---\nssmd_version: "0.9"\n---\n:::{voice="unbound"}\nHello.\n:::\n',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "episode.readio")
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())

    args = build_parser().parse_args(["plan", "roles", str(project.root)])
    assert args.func(args) == 0

    output = capsys.readouterr().out
    assert "unbound" in output
    assert "ENGINE" in output and "PROVIDER" not in output
    assert "Unresolved roles: unbound" in output
    assert "readio voices list --lang en-us" in output
    assert "readio plan bind unbound <voice>" in output


def test_plan_bind_forwards_selector_and_project_options(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "episode.ssmd"
    source.write_text(
        '---\nssmd_version: "0.9"\n---\n:::{voice="narrator"}\nHello.\n:::\n',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "episode.readio")
    calls = {}
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())

    def bind(self, project_path, role, voice, *, engine=None, discovery):
        calls["project"] = project_path
        calls["role"] = role
        calls["voice"] = voice
        calls["engine"] = engine
        calls["discovery"] = discovery
        return SimpleNamespace(
            role=role,
            project_binding=voice,
            project_target=VoiceTarget(engine or "pykokoro", voice),
        )

    monkeypatch.setattr(RoleService, "bind_project", bind)
    args = build_parser().parse_args(
        [
            "plan",
            "bind",
            "narrator",
            "en-us/sarah",
            str(project.root),
            "--engine",
            "pykokoro",
            "--offline",
            "--json",
        ]
    )
    assert args.func(args) == 0

    assert calls["project"] == project.root
    assert calls["role"] == "narrator"
    assert calls["voice"] == "en-us/sarah"
    assert calls["engine"] == "pykokoro"
    assert calls["discovery"].offline is True
    assert calls["discovery"].refresh is False
    payload = json.loads(capsys.readouterr().out)
    assert payload["stored_voice"] == "en-us/sarah"
    assert payload["stored_target"]["engine"] == "kokoro"
    assert "provider" not in payload["stored_target"]


def test_plan_unbind_uses_typed_mutation_result(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "episode.ssmd"
    source.write_text(
        '---\nssmd_version: "0.9"\n---\n:::{voice="narrator"}\nHello.\n:::\n',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "episode.readio")
    calls = {}
    mutation = ProjectRoleMutationResult(
        project=ProjectRef(
            root=project.root,
            project_id="test-project",
            name="episode",
            kind="document",
            source_format="ssmd",
        ),
        role="narrator",
        previous_project_binding="af_heart",
        project_binding=None,
        effective_voice=None,
        origin="unresolved",
        status="unresolved",
    )
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())

    def unbind_result(self, project_path, role, *, engine=None):
        calls["project"] = project_path
        calls["role"] = role
        calls["engine"] = engine
        return mutation

    monkeypatch.setattr(
        RoleService,
        "inspect_project",
        lambda *args, **kwargs: pytest.fail("CLI must not inspect before or after unbind"),
    )
    monkeypatch.setattr(
        RoleService,
        "unbind_project",
        lambda *args, **kwargs: pytest.fail("CLI must use the typed mutation operation"),
    )
    monkeypatch.setattr(RoleService, "unbind_project_result", unbind_result)
    args = build_parser().parse_args(
        [
            "plan",
            "unbind",
            "narrator",
            "--project",
            str(project.root),
            "--engine",
            "kokoro",
            "--json",
        ]
    )
    assert args.func(args) == 0

    assert calls["project"] == project.root
    assert calls["role"] == "narrator"
    assert calls["engine"] == "kokoro"
    output = json.loads(capsys.readouterr().out)
    assert output["removed_voice"] == "af_heart"
    assert output["effective_voice"] is None
    assert output["origin"] == "unresolved"
    assert output["removed_target"] is None
    assert output["effective_target"] is None
    assert set(output) == {
        "ok",
        "project",
        "role",
        "removed_voice",
        "removed_target",
        "effective_voice",
        "effective_target",
        "origin",
    }


def test_plan_build_renderability_modes_report_structured_failures(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("First sentence.\n\n.\n\nSecond sentence.", encoding="utf-8")
    project = init_project(source, tmp_path / "source.readio")
    config = ReadioConfig(reader=ReaderSettings(spacy="off", unit="paragraph"))
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: config)

    with pytest.raises(SystemExit) as failure:
        cli.main(["plan", "build", str(project.root), "--renderability", "strict", "--json"])
    assert failure.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "planning.not_renderable"
    assert payload["details"]["issues"]
    issue = payload["details"]["issues"][0]
    assert issue["scope_id"]
    assert issue["source_path"]
    assert issue["line"] is not None
    assert issue["column"] is not None

    with pytest.raises(SystemExit) as failure:
        cli.main(["plan", "build", str(project.root), "--renderability", "strict"])
    assert failure.value.code == 2
    human = capsys.readouterr().err
    assert "Planning attempt saved:" in human
    assert "Active semantic plan: unchanged" in human
    assert "^" in human
    assert "readio plan repair ." in human

    with pytest.raises(SystemExit) as success:
        cli.main(["plan", "build", str(project.root), "--renderability", "repair", "--json"])
    assert success.value.code == 0
    repaired = json.loads(capsys.readouterr().out)
    assert repaired["renderability_mode"] == "repair"
    assert repaired["renderability_guaranteed"] is True
    assert repaired["repairs"] > 0
    assert any(
        item["code"] == "planning.renderability.repaired" for item in repaired["diagnostics"]
    )

    with pytest.raises(SystemExit) as success:
        cli.main(["plan", "build", str(project.root), "--renderability", "repair"])
    assert success.value.code == 0
    human_repaired = capsys.readouterr().out
    assert "Renderability: guaranteed (repair)" in human_repaired
    assert "Repairs: " in human_repaired
    assert "document: document/" in human_repaired

    with pytest.raises(SystemExit) as default_build:
        cli.main(["plan", "build", str(project.root), "--json"])
    assert default_build.value.code == 0
    assert json.loads(capsys.readouterr().out)["renderability_mode"] == "repair"


def test_plan_inspect_and_repair_cli_commands(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "repair-cli.txt"
    source.write_text("First sentence.\n\n.\n\nLast sentence.", encoding="utf-8")
    project = init_project(source, tmp_path / "repair-cli.readio")
    monkeypatch.setattr(
        cli,
        "_resolved_config",
        lambda _args: ReadioConfig(reader=ReaderSettings(spacy="off", unit="paragraph")),
    )

    with pytest.raises(SystemExit) as blocked:
        cli.main(["plan", "build", str(project.root), "--renderability", "strict", "--json"])
    assert blocked.value.code == 2
    error = json.loads(capsys.readouterr().out)
    assert error["details"]["attempt_id"]

    with pytest.raises(SystemExit) as inspected:
        cli.main(["plan", "inspect", str(project.root), "--issues", "--repairs", "--json"])
    assert inspected.value.code == 0
    inspection = json.loads(capsys.readouterr().out)
    assert inspection["selected"] == "attempt"
    assert inspection["issues"]
    assert inspection["repairs"]

    with pytest.raises(SystemExit) as dry_run:
        cli.main(["plan", "repair", str(project.root), "--dry-run", "--json"])
    assert dry_run.value.code == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True
    assert preview["activated"] is False

    with pytest.raises(SystemExit) as repaired:
        cli.main(["plan", "repair", str(project.root), "--json"])
    assert repaired.value.code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["activated"] is True
    assert result["repairs"] > 0
    assert result["source_files_changed"] is False


def test_plan_build_cli_keeps_front_mattered_scene_separators_out_of_speech(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "scene-cli.ssmd"
    source.write_text(
        '---\nssmd_version: "0.9"\nlanguage: en-US\n---\n'
        "Before the break.\n\n---\n\nAfter the break.\n\n...p",
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "scene-cli.readio")
    monkeypatch.setattr(
        cli,
        "_resolved_config",
        lambda _args: ReadioConfig(reader=ReaderSettings(spacy="off")),
    )

    with pytest.raises(SystemExit) as built:
        cli.main(["plan", "build", str(project.root), "--renderability", "strict", "--json"])

    assert built.value.code == 0
    assert json.loads(capsys.readouterr().out)["activated"] is True
    plan = load_primary_scope_plan(project)
    assert preflight_renderability(plan).ok
    assert [segment.text for segment in plan.segments] == ["Before the break.", "After the break."]


def test_plan_commands_discover_projects_from_nested_cwd(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "nested.ssmd"
    source.write_text(
        '---\nssmd_version: "0.9"\n---\n:::{voice="narrator"}\nHello.\n:::\n',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "nested.readio")
    nested = project.root / "documents" / "nested"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())
    calls = {}

    def bind(self, project_path, role, voice, *, engine=None, discovery):
        calls["project"] = project_path
        return SimpleNamespace(
            role=role,
            project_binding=voice,
            project_target=VoiceTarget(engine or "pykokoro", voice),
        )

    monkeypatch.setattr(RoleService, "bind_project", bind)
    args = build_parser().parse_args(
        ["plan", "bind", "narrator", "af_sarah", "--engine", "pykokoro", "--json"]
    )
    assert args.func(args) == 0
    assert calls["project"] == nested
    assert json.loads(capsys.readouterr().out)["project"] == str(project.root)

    roles_args = build_parser().parse_args(["plan", "roles", "--json"])
    assert roles_args.func(roles_args) == 0
    assert json.loads(capsys.readouterr().out)["project"] == str(project.root)


def test_plan_binding_rejects_positional_and_option_projects(tmp_path, monkeypatch) -> None:
    source = tmp_path / "conflict.ssmd"
    source.write_text("Hello.\n", encoding="utf-8")
    project = init_project(source, tmp_path / "conflict.readio")
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())
    args = build_parser().parse_args(
        [
            "plan",
            "unbind",
            "narrator",
            str(project.root),
            "--project",
            str(project.root),
        ]
    )
    with pytest.raises(ValueError, match="specify the project once"):
        args.func(args)


def test_render_dry_run_still_resolves_one_shot_text(capsys) -> None:
    args = build_parser().parse_args(["render", "Hello world", "--dry-run"])
    assert args.func(args) == 0
    output = capsys.readouterr().out
    assert "Planning" in output
    assert "Semantic plan" in output


def test_render_dry_run_json_still_emits_execution_plan(capsys) -> None:
    args = build_parser().parse_args(["render", "Hello world", "--dry-run", "--json"])
    assert args.func(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "readio.plan.v2"
    assert result["ok"] is True
    assert "render" in result


def test_render_dry_run_rejects_voice_discovery(tmp_path) -> None:
    source = tmp_path / "cast.ssmd"
    source.write_text(
        '---\nssmd_version: "0.9"\n---\n:::{voice="host"}\nHello.\n:::\n',
        encoding="utf-8",
    )
    args = build_parser().parse_args(["render", str(source), "--dry-run", "--resolve-voices"])
    with pytest.raises(ValueError, match="not available during plan/dry-run"):
        args.func(args)
