"""readio: a terminal streaming text-to-speech reader."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("readio")
except PackageNotFoundError:  # running directly from an unpacked source tree
    __version__ = "0+unknown"


from .skills import skill_path

__all__ = ["__version__", "skill_path"]
