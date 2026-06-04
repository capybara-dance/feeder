from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd
from pykrx import stock

from ..provider import DataProvider
from .provider_utils import load_master_json


@dataclass(frozen=True)
class PykrxProvider(DataProvider):
    """
    DataProvider implementation:
    - tickers/master: local Seibro-derived JSON
    - ohlcv: pykrx
    """

    master_json_path: str
    name: str = "pykrx"

    def load_stock_master(self, *, asof_date: dt.date | None = None) -> pd.DataFrame:
        # asof_date reserved for future providers
        return load_master_json(self.master_json_path)

    def list_tickers(
        self,
        *,
        asof_date: dt.date | None = None,
        market: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        master = self.load_stock_master(asof_date=asof_date)
        if market:
            m = str(market).strip()
            master = master[master["Market"] == m]
        tickers = master["Code"].astype(str).str.zfill(6).unique().tolist()
        tickers = sorted(tickers)
        market_by_ticker = dict(zip(master["Code"].tolist(), master["Market"].tolist()))
        return tickers, market_by_ticker

    def fetch_ohlcv(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        # pykrx returns DatetimeIndex + korean column names
        return stock.get_market_ohlcv(start_date, end_date, str(ticker).zfill(6), adjusted=bool(adjusted))

