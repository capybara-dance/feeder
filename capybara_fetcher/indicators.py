from __future__ import annotations

import pandas as pd


MA_WINDOWS = [5, 10, 20, 60, 120, 200]
NEW_HIGH_WINDOW_TRADING_DAYS = 252
NEW_LOW_WINDOW_TRADING_DAYS = 252
MANSFIELD_RS_SMA_WINDOW = 200

# Multi-timeframe Mansfield RS SMA windows (trading days)
MRS_WINDOWS = {
    "MRS_1M": 21,   # ~1 month
    "MRS_3M": 63,   # ~3 months
    "MRS_6M": 126,  # ~6 months
    "MRS_12M": 250, # 12 months / 1 year
}


def compute_features(
    ohlcv_df: pd.DataFrame,
    *,
    benchmark_close_by_date: pd.Series | None,
) -> pd.DataFrame:
    """
    Add feature columns to standardized OHLCV.

    Expected input:
      Date, Open, High, Low, Close, Volume, TradingValue, Change, Ticker
    """
    if ohlcv_df is None or ohlcv_df.empty:
        raise ValueError("ohlcv_df is empty")

    df = ohlcv_df.copy()
    if "Date" not in df.columns or "Close" not in df.columns:
        raise ValueError("ohlcv_df missing Date/Close")

    df = df.sort_values("Date")
    close = pd.to_numeric(df["Close"], errors="raise")
    high = pd.to_numeric(df["High"], errors="raise")
    low = pd.to_numeric(df["Low"], errors="raise")

    # Moving averages
    # Optimize to float32 for storage efficiency (sufficient precision for financial indicators)
    for w in MA_WINDOWS:
        df[f"SMA_{w}"] = close.rolling(window=w, min_periods=w).mean().astype("float32")

    # Mansfield Relative Strength (vs benchmark)
    if benchmark_close_by_date is not None and not benchmark_close_by_date.empty:
        # Pandas requires unique index for fast mapping; enforce here (fail-fast elsewhere).
        # If duplicates exist, keep the last observed value for each date.
        if not benchmark_close_by_date.index.is_unique:
            benchmark_close_by_date = benchmark_close_by_date[~benchmark_close_by_date.index.duplicated(keep="last")]

        bench = df["Date"].dt.normalize().map(benchmark_close_by_date)
        bench = pd.to_numeric(bench, errors="coerce")
        rs_raw = close / bench
        
        # Original MansfieldRS (200-day window)
        rs_sma = rs_raw.rolling(window=MANSFIELD_RS_SMA_WINDOW, min_periods=MANSFIELD_RS_SMA_WINDOW).mean()
        # Optimize to float32 for storage efficiency
        df["MansfieldRS"] = ((rs_raw / rs_sma - 1.0) * 100.0).astype("float32")
        
        # Multi-timeframe MRS (raw values, percentiles calculated later in orchestrator)
        for col_name, window in MRS_WINDOWS.items():
            rs_sma_n = rs_raw.rolling(window=window, min_periods=window).mean()
            df[f"{col_name}_raw"] = ((rs_raw / rs_sma_n - 1.0) * 100.0).astype("float32")
    else:
        df["MansfieldRS"] = pd.NA
        for col_name in MRS_WINDOWS.keys():
            df[f"{col_name}_raw"] = pd.NA

    # 1Y new high (High is the highest high in last ~1 year trading days, inclusive)
    roll_max = high.rolling(window=NEW_HIGH_WINDOW_TRADING_DAYS, min_periods=NEW_HIGH_WINDOW_TRADING_DAYS).max()
    df["IsNewHigh1Y"] = high.eq(roll_max).astype("boolean")

    # 1Y new low (Low is the lowest low in last ~1 year trading days, inclusive)
    roll_min = low.rolling(window=NEW_LOW_WINDOW_TRADING_DAYS, min_periods=NEW_LOW_WINDOW_TRADING_DAYS).min()
    df["IsNewLow1Y"] = low.eq(roll_min).astype("boolean")

    return df

