"""readio: a terminal streaming text-to-speech reader."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("readio")
except PackageNotFoundError:  # running directly from an unpacked source tree
    __version__ = "0+unknown"

__all__ = ["__version__"]
