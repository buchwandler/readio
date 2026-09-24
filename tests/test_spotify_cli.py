from __future__ import annotations

import pytest

from readio import cli, spotify_cli
from readio.spotify import SpotifyCommandError, SpotifyProtocolError


def test_spotify_publish_normalizes_input_before_public_service_call(monkeypatch, tmp_path):
    source = tmp_path / "episode.ssmd"
    source.write_text('<div voice="host">Hello.</div>', encoding="utf-8")
    args = cli.build_parser().parse_args(["spotify", "publish", str(source), "--title", "Episode"])
    seen = {}

    class Service:
        def __init__(self, app):
            pass

        def publish(self, request, *, on_event=None):
            seen["request"] = request
            raise RuntimeError("stop after request")

    monkeypatch.setattr(spotify_cli, "SpotifyService", Service)

    with pytest.raises(RuntimeError, match="stop after request"):
        spotify_cli.cmd_spotify_publish(args)

    assert seen["request"].render.input.document.source_path == source
    assert not hasattr(spotify_cli, "_cli")


def test_spotify_command_family_routes():
    parser = cli.build_parser()
    assert (
        parser.parse_args(["spotify", "publish", "text", "--title", "Episode"]).spotify_command
        == "publish"
    )
    assert (
        parser.parse_args(
            ["spotify", "upload", "episode.mp3", "--title", "Episode"]
        ).spotify_command
        == "upload"
    )
    assert parser.parse_args(["spotify", "shows"]).spotify_command == "shows"
    assert parser.parse_args(["spotify", "status", "abc"]).spotify_command == "status"
    assert parser.parse_args(["spotify", "doctor"]).spotify_command == "doctor"


def test_clean_break_rejects_bare_spotify_publisher():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["spotify", "text", "--title", "Episode"])


def test_wait_accepts_optional_duration_and_deprecated_alias():
    parser = cli.build_parser()
    assert parser.parse_args(["spotify", "status", "abc", "--wait"]).wait == ""
    assert parser.parse_args(["spotify", "status", "abc", "--wait", "2m"]).wait == "2m"
    assert (
        parser.parse_args(["spotify", "status", "abc", "--wait-timeout", "2m"]).wait_timeout_compat
        == "2m"
    )


def test_global_json_is_position_independent_in_main(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_cmd_doctor", lambda args: print('{"ok": true}') or 0)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--json", "doctor"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out == '{"ok": true}\n'


def test_adapter_error_taxonomy_is_public():
    assert issubclass(SpotifyCommandError, RuntimeError)
    assert issubclass(SpotifyProtocolError, RuntimeError)
