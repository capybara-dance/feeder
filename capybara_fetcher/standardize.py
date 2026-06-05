from __future__ import annotations

import pandas as pd


_COLUMN_ALIASES = {
    "시가": "Open",
    "고가": "High",
    "저가": "Low",
    "종가": "Close",
    "거래량": "Volume",
    "거래대금": "TradingValue",
    "시가총액": "MarketCap",
    "Open": "Open",
    "High": "High",
    "Low": "Low",
    "Close": "Close",
    "Volume": "Volume",
    "MarketCap": "MarketCap",
}


def standardize_ohlcv(raw: pd.DataFrame, *, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["Date", "Ticker", "Open", "High", "Low", "Close", "Volume", "MarketCap"])

    df = raw.copy()
    if not isinstance(df.index, pd.DatetimeIndex) and "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    elif isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index().rename(columns={df.index.name or "index": "Date"})
    else:
        df = df.reset_index().rename(columns={"index": "Date"})
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    rename_map = {c: _COLUMN_ALIASES[c] for c in df.columns if c in _COLUMN_ALIASES}
    df = df.rename(columns=rename_map)

    out_cols = ["Date", "Open", "High", "Low", "Close", "Volume", "MarketCap"]
    for c in out_cols:
        if c not in df.columns:
            df[c] = pd.NA

    for c in ["Open", "High", "Low", "Close", "Volume", "MarketCap"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    df["Ticker"] = str(ticker).zfill(6)
    df = df[["Date", "Ticker", "Open", "High", "Low", "Close", "Volume", "MarketCap"]]
    df = df.dropna(subset=["Date", "Ticker", "Close"]).sort_values("Date")
    return df


def standardize_market_cap(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize market cap frame to [Date, MarketCap]."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["Date", "MarketCap"])

    df = raw.copy()
    if isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index().rename(columns={df.index.name or "index": "Date"})
    elif "Date" not in df.columns:
        df = df.reset_index().rename(columns={"index": "Date"})

    rename_map = {c: _COLUMN_ALIASES[c] for c in df.columns if c in _COLUMN_ALIASES}
    df = df.rename(columns=rename_map)
    if "MarketCap" not in df.columns:
        df["MarketCap"] = pd.NA

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    df["MarketCap"] = pd.to_numeric(df["MarketCap"], errors="coerce")
    df = df[["Date", "MarketCap"]].dropna(subset=["Date"]).sort_values("Date")
    return df
