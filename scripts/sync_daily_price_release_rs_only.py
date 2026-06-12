from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

# Ensure repository root import path when run as script.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.dotenv_loader import load_dotenv_if_present


load_dotenv_if_present(Path(REPO_ROOT) / ".env")

from capybara_fetcher.db import OracleClient, OracleRepository
from capybara_fetcher.pipeline.release_ingest import (
    estimate_release_batch_count,
    iter_release_price_batches,
    prepare_release_data,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _today_kst() -> dt.date:
    return dt.datetime.now(ZoneInfo("Asia/Seoul")).date()


def main() -> None:
    parser = argparse.ArgumentParser(description="Update DAILY_PRICE RS columns only from latest release")
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--start-date", type=str, default=None, help="Optional YYYY-MM-DD override")
    parser.add_argument("--end-date", type=str, default=None, help="Optional YYYY-MM-DD override")
    parser.add_argument("--release-repo", type=str, default="capybara-dance/capybara_fetcher")
    parser.add_argument("--release-tag", type=str, default=None, help="Optional release tag (default: latest)")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    end_date = args.end_date or _today_kst().isoformat()
    if args.start_date:
        start_date = args.start_date
    else:
        lookback_days = max(0, int(args.lookback_days))
        start_date = (_today_kst() - dt.timedelta(days=lookback_days)).isoformat()

    prepared = prepare_release_data(
        repo=str(args.release_repo),
        tag=args.release_tag,
        token=os.getenv("GITHUB_TOKEN"),
    )

    total_updated = 0
    try:
        expected_batches = estimate_release_batch_count(prepared, batch_rows=200000)
        sync_started = time.monotonic()
        logger.info(
            "Starting RS-only sync: repo=%s tag=%s start=%s end=%s expected_batches=%s dry_run=%s",
            prepared.release.repo,
            prepared.release.tag,
            start_date,
            end_date,
            expected_batches,
            bool(args.dry_run),
        )

        batch_iter = iter_release_price_batches(
            prepared,
            start_date=start_date,
            end_date=end_date,
            batch_rows=200000,
        )

        if args.dry_run:
            scanned = 0
            for idx, (batch_df, _metrics) in enumerate(batch_iter, start=1):
                scanned += len(batch_df)
                elapsed = time.monotonic() - sync_started
                if expected_batches > 0:
                    pct = (idx / expected_batches) * 100
                    logger.info(
                        "Dry-run progress: batch=%s/%s(%.1f%%) scanned_rows=%s elapsed=%.1fs",
                        idx,
                        expected_batches,
                        pct,
                        scanned,
                        elapsed,
                    )
                else:
                    logger.info(
                        "Dry-run progress: batch=%s scanned_rows=%s elapsed=%.1fs",
                        idx,
                        scanned,
                        elapsed,
                    )
            logger.info("Dry-run RS-only scan done: rows=%s", scanned)
            return

        with OracleClient.from_env(batch_size=int(args.batch_size)) as client:
            repo = OracleRepository(client)
            for idx, (batch_df, _metrics) in enumerate(batch_iter, start=1):
                batch_started = time.monotonic()
                logger.info("RS-only batch start: batch=%s rows=%s", idx, len(batch_df))
                updated = int(repo.upsert_daily_price_rs(batch_df))
                total_updated += updated
                elapsed = time.monotonic() - sync_started
                batch_elapsed = time.monotonic() - batch_started
                if expected_batches > 0:
                    pct = (idx / expected_batches) * 100
                    logger.info(
                        "RS-only progress: batch=%s/%s(%.1f%%) batch_updated=%s total_updated=%s batch_sec=%.1f elapsed=%.1fs",
                        idx,
                        expected_batches,
                        pct,
                        updated,
                        total_updated,
                        batch_elapsed,
                        elapsed,
                    )
                else:
                    logger.info(
                        "RS-only progress: batch=%s batch_updated=%s total_updated=%s batch_sec=%.1f elapsed=%.1fs",
                        idx,
                        updated,
                        total_updated,
                        batch_elapsed,
                        elapsed,
                    )

        logger.info("RS-only sync completed: updated_rows=%s total_elapsed=%.1fs", total_updated, time.monotonic() - sync_started)
    finally:
        prepared.cleanup()


if __name__ == "__main__":
    main()
