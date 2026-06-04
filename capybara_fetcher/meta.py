from __future__ import annotations

import platform
import sys
from importlib.metadata import PackageNotFoundError, version as pkg_version


def _safe_pkg_version(dist_name: str) -> str | None:
    try:
        return pkg_version(dist_name)
    except PackageNotFoundError:
        return None


def build_env_meta() -> dict:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pandas": _safe_pkg_version("pandas"),
        "pyarrow": _safe_pkg_version("pyarrow"),
        "pykrx": _safe_pkg_version("pykrx"),
    }

