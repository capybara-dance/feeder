from __future__ import annotations

import datetime as dt
import importlib
from dataclasses import dataclass

import pandas as pd


_PYKRX_STOCK_MODULE = None
_PYKRX_STOCK_IMPORT_ERROR: Exception | None = None


def _get_stock_module():
    global _PYKRX_STOCK_MODULE, _PYKRX_STOCK_IMPORT_ERROR

    if _PYKRX_STOCK_MODULE is not None:
        return _PYKRX_STOCK_MODULE

    if _PYKRX_STOCK_IMPORT_ERROR is not None:
        raise RuntimeError(f"pykrx stock module unavailable: {_PYKRX_STOCK_IMPORT_ERROR}") from _PYKRX_STOCK_IMPORT_ERROR

    try:
        _PYKRX_STOCK_MODULE = importlib.import_module("pykrx.stock")
        return _PYKRX_STOCK_MODULE
    except Exception as exc:
        _PYKRX_STOCK_IMPORT_ERROR = exc
        raise RuntimeError(f"pykrx stock module unavailable: {exc}") from exc


@dataclass(frozen=True)
class PykrxProvider:
    name: str = "pykrx"

    def fetch_ohlcv(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        start = start_date.replace("-", "")
        end = end_date.replace("-", "")
        stock = _get_stock_module()
        return stock.get_market_ohlcv_by_date(start, end, ticker, adjusted=adjusted)

    def fetch_market_cap(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        start = start_date.replace("-", "")
        end = end_date.replace("-", "")
        stock = _get_stock_module()
        return stock.get_market_cap_by_date(start, end, ticker)

    def fetch_ohlcv_bulk(
        self,
        *,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """Fetch OHLCV for all tickers (KOSPI + KOSDAQ) for each trading date in the range.

        Returns a long-format DataFrame with columns: Date, Ticker, plus raw pykrx columns.
        Much more efficient than per-ticker calls when collecting many tickers.
        """
        start = dt.date.fromisoformat(start_date[:10])
        end = dt.date.fromisoformat(end_date[:10])
        stock = _get_stock_module()

        frames: list[pd.DataFrame] = []
        cur = start
        while cur <= end:
            if cur.weekday() >= 5:  # skip weekends
                cur += dt.timedelta(days=1)
                continue
            date_str = cur.strftime("%Y%m%d")
            for market in ("KOSPI", "KOSDAQ"):
                try:
                    df = stock.get_market_ohlcv_by_ticker(date_str, market=market, adjusted=adjusted)
                    if df is not None and not df.empty:
                        df = df.copy()
                        df.index.name = "Ticker"
                        df = df.reset_index()
                        df["Date"] = pd.Timestamp(cur)
                        frames.append(df)
                except Exception:
                    pass
            cur += dt.timedelta(days=1)

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def fetch_market_cap_bulk(
        self,
        *,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Fetch market cap for all tickers (KOSPI + KOSDAQ) for each trading date in the range.

        Returns a long-format DataFrame with columns: Date, Ticker, plus raw pykrx columns.
        """
        start = dt.date.fromisoformat(start_date[:10])
        end = dt.date.fromisoformat(end_date[:10])
        stock = _get_stock_module()

        frames: list[pd.DataFrame] = []
        cur = start
        while cur <= end:
            if cur.weekday() >= 5:
                cur += dt.timedelta(days=1)
                continue
            date_str = cur.strftime("%Y%m%d")
            for market in ("KOSPI", "KOSDAQ"):
                try:
                    df = stock.get_market_cap_by_ticker(date_str, market=market)
                    if df is not None and not df.empty:
                        df = df.copy()
                        df.index.name = "Ticker"
                        df = df.reset_index()
                        df["Date"] = pd.Timestamp(cur)
                        frames.append(df)
                except Exception:
                    pass
            cur += dt.timedelta(days=1)

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def load_stock_master(self, *, asof_date: dt.date | None = None) -> pd.DataFrame:
        _ = asof_date
        raise NotImplementedError("PykrxProvider does not provide stock master")

    def list_tickers(
        self,
        *,
        asof_date: dt.date | None = None,
        market: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        _ = asof_date, market
        raise NotImplementedError("PykrxProvider does not provide ticker list in this architecture")
