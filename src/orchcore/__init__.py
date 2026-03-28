"""orchcore -- Reusable orchestration core for AI coding agent CLI pipelines."""

from importlib.metadata import PackageNotFoundError, version

__version__: str
try:
    __version__ = version("orchcore")
except PackageNotFoundError:
    __version__ = "0.0.0"
