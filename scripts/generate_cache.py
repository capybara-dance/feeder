import argparse
import datetime as dt
import logging
import os
import sys
from time import perf_counter

# Ensure repository root is on sys.path when executed as a script.
# (GitHub Actions runs: `python scripts/generate_cache.py ...`)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Configure logging to show INFO level messages
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)

from capybara_fetcher.orchestrator import (
    CacheBuildConfig,
    TickerProcessingError,
    build_failure_meta,
    run_cache_build,
    INDUSTRY_BENCHMARK_069500,
    INDUSTRY_BENCHMARK_UNIVERSE,
)
from capybara_fetcher.io_utils import write_json
from capybara_fetcher.providers import PykrxProvider, KoreaInvestmentProvider, FdrProvider, CompositeProvider

def main():
    parser = argparse.ArgumentParser(description="Generate Korea Universe Feature Cache (fail-fast)")
    parser.add_argument(
        "--start-date",
        type=str,
        default=(dt.datetime.now() - dt.timedelta(days=365 * 3)).strftime("%Y%m%d"),
        help="Start date (YYYYMMDD)",
    )
    parser.add_argument("--end-date", type=str, default=dt.datetime.now().strftime("%Y%m%d"), help="End date (YYYYMMDD)")
    parser.add_argument("--output", type=str, default="korea_universe_feature_frame.parquet", help="Output parquet file path")
    parser.add_argument("--meta-output", type=str, default="", help="Output metadata json file path (default: <output>.meta.json)")
    parser.add_argument("--industry-output", type=str, default="", help="Output industry parquet file path (optional)")
    parser.add_argument("--industry-meta-output", type=str, default="", help="Output industry metadata json path (optional)")
    parser.add_argument(
        "--industry-benchmark",
        type=str,
        default=INDUSTRY_BENCHMARK_UNIVERSE,
        choices=[INDUSTRY_BENCHMARK_UNIVERSE, INDUSTRY_BENCHMARK_069500],
        help="Industry Mansfield RS benchmark: 'universe' or '069500'",
    )
    parser.add_argument(
        "--krx-stock-master-json",
        type=str,
        default="/workspace/data/krx_stock_master.json",
        help="Path to krx_stock_master.json (used by data provider for universe/master)",
    )
    parser.add_argument("--max-workers", type=int, default=8, help="Number of threads")
    parser.add_argument("--test-limit", type=int, default=0, help="Limit number of tickers for testing (0 for all)")
    parser.add_argument(
        "--provider",
        type=str,
        default="composite",
        choices=["composite", "pykrx", "korea_investment", "fdr"],
        help="Data provider to use: 'composite' (default), 'pykrx', 'korea_investment', or 'fdr'",
    )
    parser.add_argument(
        "--ki-appkey",
        type=str,
        default=None,
        help="Korea Investment API appkey (required if --provider=korea_investment, can use env HT_KE)",
    )
    parser.add_argument(
        "--ki-appsecret",
        type=str,
        default=None,
        help="Korea Investment API appsecret (required if --provider=korea_investment, can use env HT_SE)",
    )

    args = parser.parse_args()

    meta_output = args.meta_output or f"{args.output}.meta.json"
    industry_output = args.industry_output.strip() or None
    industry_meta_output = args.industry_meta_output.strip() or None
    if industry_output and not industry_meta_output:
        industry_meta_output = f"{industry_output}.meta.json"
    if industry_meta_output and not industry_output:
        industry_meta_output = None

    cfg = CacheBuildConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        output_path=args.output,
        meta_output_path=meta_output,
        industry_output_path=industry_output,
        industry_meta_output_path=industry_meta_output,
        industry_benchmark=args.industry_benchmark,
        max_workers=int(args.max_workers),
        test_limit=int(args.test_limit),
        adjusted=True,
    )

    # Create provider based on --provider argument
    if args.provider == "composite":
        provider = CompositeProvider()
        print(f"[INFO] Using Composite provider")
    elif args.provider == "korea_investment":
        # Get credentials from arguments or environment variables
        appkey = args.ki_appkey or os.environ.get("HT_KE")
        appsecret = args.ki_appsecret or os.environ.get("HT_SE")
        
        if not appkey or not appsecret or not appkey.strip() or not appsecret.strip():
            raise ValueError(
                "Korea Investment provider requires --ki-appkey and --ki-appsecret arguments "
                "or HT_KE and HT_SE environment variables (non-empty strings)"
            )
        
        provider = KoreaInvestmentProvider(
            master_json_path=args.krx_stock_master_json,
            appkey=appkey.strip(),
            appsecret=appsecret.strip(),
        )
        print(f"[INFO] Using Korea Investment provider")
    elif args.provider == "fdr":
        provider = FdrProvider(
            master_json_path=args.krx_stock_master_json,
            source="KRX",
        )
        print(f"[INFO] Using FDR provider (source: KRX)")
    else:  # pykrx
        provider = PykrxProvider(master_json_path=args.krx_stock_master_json)
        print(f"[INFO] Using Pykrx provider")

    t0 = perf_counter()
    started_at = dt.datetime.now(dt.timezone.utc)
    try:
        run_cache_build(cfg, provider=provider)
    except Exception as e:
        ticker = e.ticker if isinstance(e, TickerProcessingError) else None
        stage = e.stage if isinstance(e, TickerProcessingError) else "run_cache_build"
        meta = build_failure_meta(
            cfg=cfg,
            provider=provider,
            started_at_utc=started_at,
            stage=stage,
            error=e,
            ticker=ticker,
            timing_seconds={"total": round(perf_counter() - t0, 4)},
        )
        write_json(meta, cfg.meta_output_path)
        raise

    print(f"Done. Total time: {perf_counter() - t0:.2f}s")

if __name__ == "__main__":
    main()
