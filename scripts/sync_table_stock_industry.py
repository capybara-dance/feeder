from __future__ import annotations

import os
import subprocess
import sys


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    cmd = [
        sys.executable,
        os.path.join(REPO_ROOT, "scripts", "sync_oracle.py"),
        "--source",
        "collect",
        "--mode",
        "range",
        "--start-date",
        "2024-01-01",
        "--end-date",
        "2024-01-01",
        "--tables",
        "industry",
        "--skip-dividends",
    ]
    cmd.extend(sys.argv[1:])
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
