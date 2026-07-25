"""ci-doctor: a read-only postmortem analyzer for failed CI runs."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ci-doctorr")  # PyPI distribution name (import pkg stays ci_doctor)
except PackageNotFoundError:  # not installed (e.g. running from a raw checkout)
    __version__ = "0.0.0"
