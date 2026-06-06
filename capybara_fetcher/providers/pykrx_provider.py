from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd
from pykrx import stock


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
        return stock.get_market_cap_by_date(start, end, ticker)

    def fetch_index_fundamental(
        self,
        *,
        start_date: str,
        end_date: str,
        index_code: str,
    ) -> pd.DataFrame:
        start = start_date.replace("-", "")
        end = end_date.replace("-", "")
        return stock.get_index_fundamental(start, end, index_code)

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
