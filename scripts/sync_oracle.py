from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys

# Ensure repository root import path when run as script.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from capybara_fetcher.pipeline import CollectionConfig, collect_data

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _to_iso_date(v: str) -> str:
    s = str(v).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    dt.date.fromisoformat(s)
    return s


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect data for Oracle sync (collection-only MVP)")
    parser.add_argument("--start-date", type=str, default=(dt.date.today() - dt.timedelta(days=3650)).strftime("%Y%m%d"))
    parser.add_argument("--end-date", type=str, default=dt.date.today().strftime("%Y%m%d"))
    parser.add_argument("--test-limit", type=int, default=50)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--market", type=str, default=None, help="Optional market filter, e.g. KOSPI/KOSDAQ/ETF")
    parser.add_argument("--master-json-path", type=str, default=None, help="Optional stock master json path")
    args = parser.parse_args()

    cfg = CollectionConfig(
        start_date=_to_iso_date(args.start_date),
        end_date=_to_iso_date(args.end_date),
        test_limit=int(args.test_limit),
        max_workers=int(args.max_workers),
        adjusted=True,
        market=args.market,
        master_json_path=args.master_json_path,
    )

    logger.info("Starting collection with CompositeProvider only")
    result = collect_data(cfg)

    logger.info("industry_df: shape=%s", result.industry_df.shape)
    logger.info("master_df: shape=%s", result.master_df.shape)
    logger.info("price_df: shape=%s", result.price_df.shape)
    logger.info("dividend_df: shape=%s", result.dividend_df.shape)


if __name__ == "__main__":
    main()
