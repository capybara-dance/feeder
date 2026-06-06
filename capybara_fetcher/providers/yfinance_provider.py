from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import yfinance as yf


@dataclass(frozen=True)
class YFinanceProvider:
    """Dividend fetcher backed by yfinance."""

    name: str = "yfinance"

    def fetch_dividends(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        start_dt = pd.to_datetime(start_date).tz_localize(None)
        end_dt = pd.to_datetime(end_date).tz_localize(None)

        # KRX tickers are typically mapped as .KS (KOSPI/ETF) or .KQ (KOSDAQ).
        symbols = [f"{str(ticker).zfill(6)}.KS", f"{str(ticker).zfill(6)}.KQ"]
        frames: list[pd.DataFrame] = []

        for symbol in symbols:
            try:
                div = yf.Ticker(symbol).dividends
            except Exception:
                continue

            if div is None or len(div) == 0:
                continue

            df = div.reset_index()
            if df.empty:
                continue

            date_col = df.columns[0]
            value_col = df.columns[1]
            df = df.rename(columns={date_col: "Date", value_col: "Dividend"})
            date_series = pd.to_datetime(df["Date"], errors="coerce")
            if getattr(date_series.dt, "tz", None) is not None:
                date_series = date_series.dt.tz_localize(None)
            df["Date"] = date_series.dt.normalize()
            df["Dividend"] = pd.to_numeric(df["Dividend"], errors="coerce")
            df = df.dropna(subset=["Date", "Dividend"])
            df = df[(df["Date"] >= start_dt) & (df["Date"] <= end_dt)]
            if not df.empty:
                frames.append(df[["Date", "Dividend"]])

        if not frames:
            return pd.DataFrame(columns=["Date", "Dividend"])

        out = pd.concat(frames, ignore_index=True)
        out = out.drop_duplicates(subset=["Date"], keep="first").sort_values("Date").reset_index(drop=True)
        return out
