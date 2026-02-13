"""Azure Availability Zone Mapping Viewer."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("az-mapping")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"
