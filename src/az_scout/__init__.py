"""Azure Scout."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("az-scout")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"
