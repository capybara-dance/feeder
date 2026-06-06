from __future__ import annotations

import pandas as pd


_PYKRX_RENAME_MAP = {
    "시가": "Open",
    "고가": "High",
    "저가": "Low",
    "종가": "Close",
    "거래량": "Volume",
    "거래대금": "TradingValue",
    "등락률": "Change",
}


def standardize_ohlcv(raw_df: pd.DataFrame, *, ticker: str) -> pd.DataFrame:
    """
    Standardize provider-specific OHLCV into the project's canonical schema.

    Canonical columns:
      Date, Open, High, Low, Close, Volume, TradingValue, Change, Ticker

    Notes:
    - This function should fail early on obviously invalid inputs.
    - Do not hide exceptions; callers decide how to handle them.
    """
    if raw_df is None:
        raise ValueError("raw_df is None")
    if raw_df.empty:
        raise ValueError("raw_df is empty")

    df = raw_df.copy()

    # Normalize schema from common pykrx output (DatetimeIndex + KR columns)
    df = df.rename(columns=_PYKRX_RENAME_MAP)

    # If Date is index (pykrx), convert to column
    if "Date" not in df.columns:
        # pykrx typically returns a DatetimeIndex (name may vary by version)
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.copy()
            df.index.name = "Date"
            df = df.reset_index()

    if "Date" not in df.columns:
        raise ValueError("Missing Date column after standardization")

    df["Date"] = pd.to_datetime(df["Date"], errors="raise")
    df["Date"] = df["Date"].dt.normalize()

    # Ensure ticker formatting
    t = str(ticker).strip().zfill(6)
    df["Ticker"] = t

    # Required numeric columns (some providers may omit a subset; fail fast)
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required OHLCV columns: {missing}")

    # Optional columns
    for c in ["TradingValue", "Change"]:
        if c not in df.columns:
            df[c] = pd.NA

    # Coerce numeric columns (raise on grossly invalid)
    for c in ["Open", "High", "Low", "Close", "Volume", "TradingValue", "Change"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Optimize data types for storage efficiency (reduce parquet file size)
    # OHLC prices: int64 -> Int32 (Korean stock max price ~2.5M fits safely in int32 max 2.1B)
    df["Open"] = df["Open"].astype("Int32")
    df["High"] = df["High"].astype("Int32")
    df["Low"] = df["Low"].astype("Int32")
    df["Close"] = df["Close"].astype("Int32")

    df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
    # Enforce one row per date (keep last) to avoid downstream index collisions.
    df = df.drop_duplicates(subset=["Date"], keep="last")

    # Canonical column order
    cols = ["Date", "Open", "High", "Low", "Close", "Volume", "TradingValue", "Change", "Ticker"]
    df = df[cols].copy()

    if df.empty:
        raise ValueError("No valid rows after standardization")

    return df

