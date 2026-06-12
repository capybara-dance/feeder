from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from zoneinfo import ZoneInfo


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _today_kst() -> dt.date:
    return dt.datetime.now(ZoneInfo("Asia/Seoul")).date()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync DAILY_PRICE from latest GitHub release for recent N days")
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--start-date", type=str, default=None, help="Optional YYYY-MM-DD override")
    parser.add_argument("--end-date", type=str, default=None, help="Optional YYYY-MM-DD override")
    parser.add_argument("--release-repo", type=str, default="capybara-dance/capybara_fetcher")
    parser.add_argument("--release-tag", type=str, default=None, help="Optional release tag (default: latest)")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    end_date = args.end_date or _today_kst().isoformat()
    if args.start_date:
        start_date = args.start_date
    else:
        lookback_days = max(0, int(args.lookback_days))
        start_date = (_today_kst() - dt.timedelta(days=lookback_days)).isoformat()

    cmd = [
        sys.executable,
        os.path.join(REPO_ROOT, "scripts", "sync_oracle.py"),
        "--source",
        "release",
        "--mode",
        "range",
        "--start-date",
        start_date,
        "--end-date",
        end_date,
        "--release-repo",
        str(args.release_repo),
        "--batch-size",
        str(int(args.batch_size)),
        "--tables",
        "price",
        "--skip-dividends",
    ]

    if args.release_tag:
        cmd.extend(["--release-tag", str(args.release_tag)])
    if args.no_progress:
        cmd.append("--no-progress")
    if args.dry_run:
        cmd.append("--dry-run")

    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
