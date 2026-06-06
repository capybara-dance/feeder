from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_if_present(dotenv_path: str | Path = ".env") -> None:
    """Load simple KEY=VALUE pairs from a dotenv file into process env if absent."""
    p = Path(dotenv_path)
    if not p.exists():
        return

    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = raw.strip().strip('"').strip("'")