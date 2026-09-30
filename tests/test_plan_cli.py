"""Tests for the project plan and role-binding CLI."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import cli
from readio.api import ProjectRef, ProjectRoleMutationResult
from readio.api.projects import ProjectService
from readio.api.roles import RoleService
from readio.cli import build_parser
from readio.config import ReadioConfig
from readio.project import init_project
from readio.role_targets import VoiceTarget


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
        lambda self, project: requested.append(project) or result,
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
    assert "{build,roles,bind,unbind}" in output
    for option in ("--engine", "--voice", "--model", "--format", "--output", "--voice-bind"):
        assert option not in output


def test_plan_group_exposes_only_project_subcommands() -> None:
    parser = build_parser()
    args = parser.parse_args(["plan", "roles", "--provider", "kokoro", "--json"])
    assert args.plan_action == "roles"
    assert args.provider == "kokoro"
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
    assert result["provider"] == "kokoro"
    assert result["roles"][0]["role"] == "narrator"
    assert result["roles"][0]["origin"] == "config.voice_role"
    assert result["roles"][0]["effective_target"]["engine"] == "pykokoro"
    assert result["roles"][0]["effective_target"]["provider"] == "kokoro"


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
    assert "ENGINE" in output and "PROVIDER" in output
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

    def bind(self, project_path, role, voice, *, provider=None, engine=None, discovery):
        calls["project"] = project_path
        calls["role"] = role
        calls["voice"] = voice
        calls["provider"] = provider
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
            "--provider",
            "kokoro",
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
    assert calls["provider"] == "kokoro"
    assert calls["engine"] == "pykokoro"
    assert calls["discovery"].offline is True
    assert calls["discovery"].refresh is False
    payload = json.loads(capsys.readouterr().out)
    assert payload["stored_voice"] == "en-us/sarah"
    assert payload["stored_target"]["engine"] == "pykokoro"
    assert payload["stored_target"]["provider"] == "kokoro"


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

    def unbind_result(self, project_path, role, *, provider=None):
        calls["project"] = project_path
        calls["role"] = role
        calls["provider"] = provider
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
            "--provider",
            "kokoro",
            "--json",
        ]
    )
    assert args.func(args) == 0

    assert calls["project"] == project.root
    assert calls["role"] == "narrator"
    assert calls["provider"] == "kokoro"
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

    def bind(self, project_path, role, voice, *, provider=None, engine=None, discovery):
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
