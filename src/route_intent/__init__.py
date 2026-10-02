"""route-intent: verify that live routing state matches a declared intent."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("route-intent")
except PackageNotFoundError:
    # Running from a source tree that was never installed (no package metadata).
    # The real version lives only in pyproject.toml; this is a marker, not a version.
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
