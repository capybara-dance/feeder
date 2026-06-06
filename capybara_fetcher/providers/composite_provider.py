from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from .base import DataProvider
from .fdr_provider import FdrProvider
from .korea_investment_provider import KoreaInvestmentProvider
from .master_json_provider import MasterJsonProvider
from .pykrx_provider import PykrxProvider
from .yfinance_provider import YFinanceProvider


@dataclass(frozen=True)
class CompositeProvider(DataProvider):
    """External-facing provider. Internal providers remain hidden."""

    name: str = "composite"
    master_json_path: str | None = None
    _master_provider: MasterJsonProvider = field(default=None, init=False, repr=False, compare=False)
    _fdr_provider: FdrProvider = field(default=None, init=False, repr=False, compare=False)
    _pykrx_provider: PykrxProvider = field(default=None, init=False, repr=False, compare=False)
    _korea_investment_provider: KoreaInvestmentProvider = field(default=None, init=False, repr=False, compare=False)
    _yfinance_provider: YFinanceProvider = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_master_provider", MasterJsonProvider(master_json_path=self.master_json_path))
        object.__setattr__(self, "_fdr_provider", FdrProvider(source="KRX"))
        object.__setattr__(self, "_pykrx_provider", PykrxProvider())
        object.__setattr__(self, "_korea_investment_provider", KoreaInvestmentProvider())
        object.__setattr__(self, "_yfinance_provider", YFinanceProvider())

    def load_stock_master(self, *, asof_date: dt.date | None = None) -> pd.DataFrame:
        master = object.__getattribute__(self, "_master_provider")
        return master.load_stock_master(asof_date=asof_date)

    def list_tickers(
        self,
        *,
        asof_date: dt.date | None = None,
        market: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        fdr_provider = object.__getattribute__(self, "_fdr_provider")
        try:
            tickers, market_map = fdr_provider.list_tickers(asof_date=asof_date, market=market)
            if tickers:
                return tickers, market_map
        except Exception:
            pass

        master = object.__getattribute__(self, "_master_provider")
        return master.list_tickers(asof_date=asof_date, market=market)

    def fetch_ohlcv(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        pykrx_provider = object.__getattribute__(self, "_pykrx_provider")
        try:
            df = pykrx_provider.fetch_ohlcv(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
                adjusted=adjusted,
            )
            if df is not None and not df.empty:
                return df
        except Exception:
            pass

        fdr_provider = object.__getattribute__(self, "_fdr_provider")
        return fdr_provider.fetch_ohlcv(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            adjusted=adjusted,
        )

    def fetch_market_cap(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        pykrx_provider = object.__getattribute__(self, "_pykrx_provider")
        try:
            df = pykrx_provider.fetch_market_cap(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
            )
            if df is not None and not df.empty:
                return df
        except Exception:
            pass

        return pd.DataFrame()

    def fetch_market_cap_snapshot(self, *, ticker: str) -> float | None:
        kis_provider = object.__getattribute__(self, "_korea_investment_provider")
        try:
            return kis_provider.fetch_market_cap_snapshot(ticker)
        except Exception:
            return None

    def fetch_dividends(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        yfinance_provider = object.__getattribute__(self, "_yfinance_provider")
        try:
            return yfinance_provider.fetch_dividends(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception:
            return pd.DataFrame(columns=["Date", "Dividend"])
