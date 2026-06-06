from __future__ import annotations

from typing import Protocol
import datetime as dt

import pandas as pd


class DataProvider(Protocol):
    """
    Single provider contract.

    A provider is responsible for supplying:
    - universe (tickers)
    - stock master (market/industry/etc.)
    - OHLCV time series

    No fallback behavior is allowed at the orchestrator level.
    """

    name: str

    def list_tickers(
        self,
        *,
        asof_date: dt.date | None = None,
        market: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        """
        Returns:
          - tickers: list of 6-digit strings
          - market_by_ticker: mapping ticker -> market label (if known)
        """

    def load_stock_master(
        self,
        *,
        asof_date: dt.date | None = None,
    ) -> pd.DataFrame:
        """
        Returns a DataFrame that includes at least:
          Code, Name, Market, IndustryLarge, IndustryMid, IndustrySmall, SharesOutstanding
        """

    def fetch_ohlcv(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV for a ticker and date range.

        The returned shape is provider-specific (raw).
        Standardization is handled elsewhere.
        """

