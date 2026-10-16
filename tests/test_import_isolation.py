from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _run(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=child_env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_importing_public_api_does_not_load_native_book_stack() -> None:
    _run(
        """
import sys
import readio
import readio.api
from readio.api import Readio
assert 'readio.book_pipeline' not in sys.modules
assert 'audiocompose' not in sys.modules
assert 'audioexport' not in sys.modules
"""
    )


def test_book_compatibility_exports_are_lazy() -> None:
    _run(
        """
import sys
import readio.api
assert 'readio.book_pipeline' not in sys.modules
from readio.api import BookBuildRequest
assert BookBuildRequest is not None
assert 'readio.book_pipeline' in sys.modules
"""
    )


def test_cli_starts_with_audiocompose_without_compose_book(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    package = shadow / "audiocompose"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("__version__ = '0.2.2'\n", encoding="utf-8")
    env = {"PYTHONPATH": os.pathsep.join((str(shadow), os.environ.get("PYTHONPATH", "")))}

    for arguments in (("--version",), ("--help",), ("speak", "--help")):
        result = subprocess.run(
            [sys.executable, "-m", "readio", *arguments],
            cwd=ROOT,
            env={**os.environ, **env},
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout or result.stderr


def test_exact_speech_selector_reaches_speech_path_without_book_pipeline(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    package = shadow / "audiocompose"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("__version__ = '0.2.2'\n", encoding="utf-8")
    env = {"PYTHONPATH": os.pathsep.join((str(shadow), os.environ.get("PYTHONPATH", "")))}
    _run(
        """
import sys
from types import SimpleNamespace
from readio import cli

class Progress:
    public_event = None
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False

calls = []
app = SimpleNamespace(
    config=SimpleNamespace(reader=SimpleNamespace(lang='en')),
    speech=SimpleNamespace(speak=lambda request, on_event=None: calls.append(request)),
)
cli._api_for = lambda _args: app
cli._build_plan_request = lambda _args, _app, **_kwargs: object()
cli._build_progress = lambda _args: Progress()
args = cli.build_parser().parse_args([
    'speak', '--voice', 'inflect:micro-v2/default', '--lang', 'en', 'Hello, how are you?'
])
assert args.func(args) == 0
assert len(calls) == 1
assert 'readio.book_pipeline' not in sys.modules
""",
        env=env,
    )
