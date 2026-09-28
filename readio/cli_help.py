from __future__ import annotations

import argparse
from typing import Any

from rich_argparse import RichHelpFormatter


class ReadioHelpFormatter(RichHelpFormatter):
    """Readio terminal help formatter."""


class ReadioArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that applies Readio formatting to nested parsers too."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("formatter_class", ReadioHelpFormatter)
        super().__init__(*args, **kwargs)

    def add_subparsers(self, **kwargs: Any) -> Any:
        kwargs.setdefault("parser_class", ReadioArgumentParser)
        return super().add_subparsers(**kwargs)


def show_help(args: argparse.Namespace) -> int:
    """Print help for a command group invoked without a subcommand."""
    args._help_parser.print_help()
    return 0
