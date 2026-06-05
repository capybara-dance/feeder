from __future__ import annotations

import datetime as dt
from typing import Protocol

import pandas as pd


class DataProvider(Protocol):
    name: str

    def list_tickers(
        self,
        *,
        asof_date: dt.date | None = None,
        market: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        """Return ticker list and market map."""

    def load_stock_master(
        self,
        *,
        asof_date: dt.date | None = None,
    ) -> pd.DataFrame:
        """Return stock master DataFrame."""

    def fetch_ohlcv(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """Return raw OHLCV DataFrame."""
