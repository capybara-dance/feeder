from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import os
from time import perf_counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tqdm import tqdm

from .provider import DataProvider
from .standardize import standardize_ohlcv
from .indicators import compute_features, MA_WINDOWS, NEW_HIGH_WINDOW_TRADING_DAYS, NEW_LOW_WINDOW_TRADING_DAYS, MANSFIELD_RS_SMA_WINDOW, MRS_WINDOWS
from .industry import (
    INDUSTRY_LEVELS,
    compute_industry_feature_frame,
    compute_universe_equal_weight_benchmark_close_by_date,
)
from .io_utils import write_parquet, write_json
from .meta import build_env_meta


MANSFIELD_BENCHMARK_TICKER = "069500"

INDUSTRY_BENCHMARK_UNIVERSE = "universe"
INDUSTRY_BENCHMARK_069500 = "069500"


class TickerProcessingError(RuntimeError):
    def __init__(self, *, ticker: str, stage: str, cause: Exception):
        super().__init__(f"{stage} failed for ticker={ticker}: {cause}")
        self.ticker = ticker
        self.stage = stage
        self.__cause__ = cause


@dataclass(frozen=True)
class CacheBuildConfig:
    start_date: str
    end_date: str
    output_path: str
    meta_output_path: str
    industry_output_path: str | None
    industry_meta_output_path: str | None
    industry_benchmark: str
    adjusted: bool = True
    max_workers: int = 8
    test_limit: int = 0


def _file_size_mb(path: str) -> float | None:
    try:
        size = os.path.getsize(path)
        return round(size / (1024 * 1024), 4)
    except OSError:
        return None


def build_failure_meta(
    *,
    cfg: CacheBuildConfig,
    provider: DataProvider,
    started_at_utc: dt.datetime,
    stage: str,
    error: Exception,
    ticker: str | None = None,
    timing_seconds: dict | None = None,
) -> dict:
    return {
        "generated_at_utc": started_at_utc.isoformat(),
        "run_status": "failed",
        "provider": {"name": provider.name},
        "start_date": cfg.start_date,
        "end_date": cfg.end_date,
        "error": {
            "stage": stage,
            "ticker": ticker,
            "type": type(error).__name__,
            "message": str(error),
        },
        "args": {
            "adjusted": cfg.adjusted,
            "max_workers": cfg.max_workers,
            "test_limit": cfg.test_limit,
            "output": cfg.output_path,
            "meta_output": cfg.meta_output_path,
            "industry_output": cfg.industry_output_path,
            "industry_meta_output": cfg.industry_meta_output_path,
            "industry_benchmark": cfg.industry_benchmark,
        },
        "timing_seconds": timing_seconds or {},
        "env": build_env_meta(),
    }


def run_cache_build(cfg: CacheBuildConfig, *, provider: DataProvider) -> dict:
    """
    Main pipeline (fail-fast).

    This function raises on any error.
    Callers (CLI) should catch at the boundary to write failure meta.
    """
    t0 = perf_counter()
    started_at = dt.datetime.now(dt.timezone.utc)

    # 1) Universe + master
    print(f"[TIMING] Loading universe and master data...")
    t_univ0 = perf_counter()
    master_df = provider.load_stock_master()
    tickers, _market_by_ticker = provider.list_tickers()
    t_univ1 = perf_counter()
    print(f"[TIMING] Universe/master loaded: {t_univ1 - t_univ0:.2f}s ({len(tickers)} tickers)")

    if cfg.test_limit > 0:
        tickers = tickers[: cfg.test_limit]
    if not tickers:
        raise ValueError("no tickers returned by provider")

    # 2) Benchmark for Mansfield RS
    print(f"[TIMING] Fetching benchmark data for Mansfield RS...")
    t_bench0 = perf_counter()
    bench_raw = provider.fetch_ohlcv(
        ticker=MANSFIELD_BENCHMARK_TICKER,
        start_date=cfg.start_date,
        end_date=cfg.end_date,
        adjusted=cfg.adjusted,
    )
    bench_std = standardize_ohlcv(bench_raw, ticker=MANSFIELD_BENCHMARK_TICKER)
    benchmark_close = pd.to_numeric(bench_std["Close"], errors="coerce")
    benchmark_close.index = bench_std["Date"]
    benchmark_close = benchmark_close.dropna()
    if benchmark_close.empty:
        raise ValueError("benchmark close series is empty after standardization")
    t_bench1 = perf_counter()
    print(f"[TIMING] Benchmark fetched: {t_bench1 - t_bench0:.2f}s")

    # 3) Per-ticker fetch -> standardize -> features (parallel or sequential with progress bar)
    print(f"[TIMING] Fetching OHLCV data for {len(tickers)} tickers (max_workers={cfg.max_workers})...")
    t_fetch0 = perf_counter()

    def fetch_one(ticker: str) -> pd.DataFrame:
        raw = provider.fetch_ohlcv(
            ticker=ticker,
            start_date=cfg.start_date,
            end_date=cfg.end_date,
            adjusted=cfg.adjusted,
        )
        std = standardize_ohlcv(raw, ticker=ticker)
        feat = compute_features(std, benchmark_close_by_date=benchmark_close)
        return feat

    frames: list[pd.DataFrame] = []
    
    # Use sequential processing with tqdm when max_workers=1
    if cfg.max_workers == 1:
        print("[INFO] Running in sequential mode with progress tracking...")
        for ticker in tqdm(tickers, desc="Fetching tickers", unit="ticker"):
            try:
                frames.append(fetch_one(ticker))
            except Exception as e:
                raise TickerProcessingError(ticker=ticker, stage="fetch/standardize/feature", cause=e) from e
    else:
        # Use ThreadPoolExecutor for parallel processing
        with ThreadPoolExecutor(max_workers=int(cfg.max_workers)) as ex:
            future_by_ticker = {ex.submit(fetch_one, t): t for t in tickers}
            try:
                # Wrap as_completed with tqdm for progress tracking
                for fut in tqdm(as_completed(future_by_ticker), total=len(tickers), desc="Fetching tickers", unit="ticker"):
                    t = future_by_ticker[fut]
                    try:
                        frames.append(fut.result())
                    except Exception as e:
                        raise TickerProcessingError(ticker=t, stage="fetch/standardize/feature", cause=e) from e
            except Exception:
                # Best-effort: cancel pending futures (running ones may not stop)
                for fut in future_by_ticker:
                    fut.cancel()
                raise

    t_fetch1 = perf_counter()
    print(f"[TIMING] OHLCV fetch completed: {t_fetch1 - t_fetch0:.2f}s ({len(frames)} tickers processed)")

    if not frames:
        raise ValueError("no feature frames produced")

    print(f"[TIMING] Concatenating and sorting dataframes...")
    t_concat0 = perf_counter()
    full_df = pd.concat(frames, ignore_index=True).sort_values(["Date", "Ticker"])
    t_concat1 = perf_counter()
    print(f"[TIMING] Concatenation completed: {t_concat1 - t_concat0:.2f}s (shape: {full_df.shape})")

    # Convert raw MRS values to percentile ranks (cross-sectional per date)
    print(f"[TIMING] Computing MRS percentile ranks...")
    t_pct0 = perf_counter()
    raw_cols_to_drop = []
    for col_name in MRS_WINDOWS.keys():
        raw_col = f"{col_name}_raw"
        if raw_col in full_df.columns:
            # Calculate percentile rank within each date (0-100 scale, 2 decimal places)
            full_df[col_name] = (
                full_df.groupby("Date")[raw_col]
                .rank(pct=True, method="average")
                .mul(100.0)
                .round(2)
                .astype("float32")
            )
            raw_cols_to_drop.append(raw_col)
    # Drop all raw columns at once (more efficient than dropping in loop)
    if raw_cols_to_drop:
        full_df = full_df.drop(columns=raw_cols_to_drop)
    t_pct1 = perf_counter()
    print(f"[TIMING] MRS percentile ranks computed: {t_pct1 - t_pct0:.2f}s")

    # 4) Save feature parquet
    print(f"[TIMING] Saving feature parquet to {cfg.output_path}...")
    t_save0 = perf_counter()
    write_parquet(full_df, cfg.output_path)
    t_save1 = perf_counter()
    print(f"[TIMING] Feature parquet saved: {t_save1 - t_save0:.2f}s")

    # 5) Industry cache (optional)
    industry_meta: dict | None = None
    if cfg.industry_output_path:
        print(f"[TIMING] Computing industry features...")
        t_industry0 = perf_counter()
        
        if cfg.industry_benchmark == INDUSTRY_BENCHMARK_UNIVERSE:
            global_dates = pd.to_datetime(full_df["Date"], errors="raise").dt.normalize().dropna().sort_values().unique()
            global_dates = pd.DatetimeIndex(global_dates)
            industry_benchmark_close = compute_universe_equal_weight_benchmark_close_by_date(full_df, global_dates=global_dates)
            industry_benchmark_meta = {
                "type": INDUSTRY_BENCHMARK_UNIVERSE,
                "method": "equal_weighted_daily_return_mean_then_cumprod_base_100",
            }
        elif cfg.industry_benchmark == INDUSTRY_BENCHMARK_069500:
            global_dates = pd.to_datetime(full_df["Date"], errors="raise").dt.normalize().dropna().sort_values().unique()
            global_dates = pd.DatetimeIndex(global_dates)
            industry_benchmark_close = benchmark_close
            industry_benchmark_meta = {"type": INDUSTRY_BENCHMARK_069500, "ticker": MANSFIELD_BENCHMARK_TICKER}
        else:
            raise ValueError(f"invalid industry_benchmark: {cfg.industry_benchmark}")

        ind_frames = [
            compute_industry_feature_frame(
                full_df,
                master_df=master_df,
                benchmark_close_by_date=industry_benchmark_close,
                level=lvl,
                global_dates=global_dates,
            )
            for lvl in INDUSTRY_LEVELS
        ]
        industry_df = pd.concat(ind_frames, ignore_index=True)
        
        t_industry1 = perf_counter()
        print(f"[TIMING] Industry features computed: {t_industry1 - t_industry0:.2f}s")
        
        print(f"[TIMING] Saving industry parquet to {cfg.industry_output_path}...")
        t_ind_save0 = perf_counter()
        write_parquet(industry_df, cfg.industry_output_path)
        t_ind_save1 = perf_counter()
        print(f"[TIMING] Industry parquet saved: {t_ind_save1 - t_ind_save0:.2f}s")

        industry_meta = {
            "generated_at_utc": started_at.isoformat(),
            "run_status": "success",
            "provider": {"name": provider.name},
            "start_date": cfg.start_date,
            "end_date": cfg.end_date,
            "source_feature_parquet": cfg.output_path,
            "industry_levels": INDUSTRY_LEVELS,
            "method": {
                "industry_index": "equal_weighted_daily_return_mean_then_cumprod_base_100",
                "mansfield_rs": {"benchmark": industry_benchmark_meta, "sma_window": MANSFIELD_RS_SMA_WINDOW},
            },
            "data_file": {
                "path": cfg.industry_output_path,
                "generated": True,
                "rows": int(len(industry_df)),
                "columns": list(industry_df.columns),
                "size_mb": _file_size_mb(cfg.industry_output_path),
            },
            "env": build_env_meta(),
        }

        if cfg.industry_meta_output_path:
            write_json(industry_meta, cfg.industry_meta_output_path)

    # 6) Feature meta
    meta = {
        "generated_at_utc": started_at.isoformat(),
        "run_status": "success",
        "provider": {"name": provider.name},
        "start_date": cfg.start_date,
        "end_date": cfg.end_date,
        "tickers": tickers,
        "ticker_count": len(tickers),
        "rows": int(len(full_df)),
        "columns": list(full_df.columns),
        "features": [f"SMA_{w}" for w in MA_WINDOWS] + ["MansfieldRS", "IsNewHigh1Y", "IsNewLow1Y"] + list(MRS_WINDOWS.keys()),
        "indicators": {
            "moving_averages": MA_WINDOWS,
            "mansfield_rs": {
                "benchmark_ticker": MANSFIELD_BENCHMARK_TICKER,
                "sma_window": MANSFIELD_RS_SMA_WINDOW,
            },
            "mrs_multi_timeframe": {
                "benchmark_ticker": MANSFIELD_BENCHMARK_TICKER,
                "windows": MRS_WINDOWS,
                "percentile_precision": 2,
                "description": "Cross-sectional percentile ranks (0-100.0) per date",
            },
            "new_high_1y": {"window_trading_days": NEW_HIGH_WINDOW_TRADING_DAYS},
            "new_low_1y": {"window_trading_days": NEW_LOW_WINDOW_TRADING_DAYS},
        },
        "industry_cache": {
            "enabled": bool(cfg.industry_output_path),
            "output": cfg.industry_output_path,
            "meta_output": cfg.industry_meta_output_path,
        },
        "data_file": {
            "path": cfg.output_path,
            "generated": True,
            "size_mb": _file_size_mb(cfg.output_path),
        },
        "args": {
            "adjusted": cfg.adjusted,
            "max_workers": cfg.max_workers,
            "test_limit": cfg.test_limit,
            "output": cfg.output_path,
            "meta_output": cfg.meta_output_path,
            "industry_output": cfg.industry_output_path,
            "industry_meta_output": cfg.industry_meta_output_path,
            "industry_benchmark": cfg.industry_benchmark,
        },
        "timing_seconds": {
            "universe_and_master": round(t_univ1 - t_univ0, 4),
            "benchmark_fetch": round(t_bench1 - t_bench0, 4),
            "data_fetch_and_features": round(t_fetch1 - t_fetch0, 4),
            "concat_and_sort": round(t_concat1 - t_concat0, 4),
            "mrs_percentile_ranks": round(t_pct1 - t_pct0, 4),
            "save_feature_parquet": round(t_save1 - t_save0, 4),
            "total": round(perf_counter() - t0, 4),
        },
        "env": build_env_meta(),
    }

    write_json(meta, cfg.meta_output_path)
    
    # Print timing summary
    total_time = perf_counter() - t0
    print(f"\n{'='*60}")
    print(f"[TIMING] SUMMARY")
    print(f"{'='*60}")
    print(f"  Universe/Master:      {t_univ1 - t_univ0:8.2f}s")
    print(f"  Benchmark fetch:      {t_bench1 - t_bench0:8.2f}s")
    print(f"  OHLCV fetch/features: {t_fetch1 - t_fetch0:8.2f}s")
    print(f"  Concat/Sort:          {t_concat1 - t_concat0:8.2f}s")
    print(f"  MRS percentile ranks: {t_pct1 - t_pct0:8.2f}s")
    print(f"  Save feature parquet: {t_save1 - t_save0:8.2f}s")
    if cfg.industry_output_path:
        print(f"  Industry features:    {t_industry1 - t_industry0:8.2f}s")
        print(f"  Industry save:        {t_ind_save1 - t_ind_save0:8.2f}s")
    print(f"  {'─'*58}")
    print(f"  TOTAL:                {total_time:8.2f}s")
    print(f"{'='*60}\n")
    
    return meta

