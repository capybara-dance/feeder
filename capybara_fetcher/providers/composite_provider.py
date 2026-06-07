from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
import logging
import threading

import pandas as pd

from .base import DataProvider
from .fdr_provider import FdrProvider
from .korea_investment_provider import KoreaInvestmentProvider
from .master_json_provider import MasterJsonProvider
from .pykrx_provider import PykrxProvider
from .yfinance_provider import YFinanceProvider


logger = logging.getLogger(__name__)


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
    _pykrx_lock: threading.Lock = field(default=None, init=False, repr=False, compare=False)
    _pykrx_ohlcv_available: bool | None = field(default=None, init=False, repr=False, compare=False)
    _pykrx_market_cap_available: bool | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_master_provider", MasterJsonProvider(master_json_path=self.master_json_path))
        object.__setattr__(self, "_fdr_provider", FdrProvider(source="NAVER"))
        object.__setattr__(self, "_pykrx_provider", PykrxProvider())
        object.__setattr__(self, "_korea_investment_provider", KoreaInvestmentProvider())
        object.__setattr__(self, "_yfinance_provider", YFinanceProvider())
        object.__setattr__(self, "_pykrx_lock", threading.Lock())

    def _fallback_ohlcv(self, *, ticker: str, start_date: str, end_date: str, adjusted: bool) -> pd.DataFrame:
        fdr_provider = object.__getattribute__(self, "_fdr_provider")
        return fdr_provider.fetch_ohlcv(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            adjusted=adjusted,
        )

    def _fallback_market_cap(self) -> pd.DataFrame:
        return pd.DataFrame()

    @staticmethod
    def _is_pykrx_compatible_ticker(ticker: str) -> bool:
        ticker_code = str(ticker).strip()
        return ticker_code.isdigit() and len(ticker_code) == 6

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
        lock = object.__getattribute__(self, "_pykrx_lock")
        pykrx_available = object.__getattribute__(self, "_pykrx_ohlcv_available")

        if not self._is_pykrx_compatible_ticker(ticker):
            return self._fallback_ohlcv(ticker=ticker, start_date=start_date, end_date=end_date, adjusted=adjusted)

        if pykrx_available is False:
            return self._fallback_ohlcv(ticker=ticker, start_date=start_date, end_date=end_date, adjusted=adjusted)

        if pykrx_available is None:
            with lock:
                pykrx_available = object.__getattribute__(self, "_pykrx_ohlcv_available")
                if pykrx_available is None:
                    try:
                        df = pykrx_provider.fetch_ohlcv(
                            ticker=ticker,
                            start_date=start_date,
                            end_date=end_date,
                            adjusted=adjusted,
                        )
                        if df is not None and not df.empty:
                            object.__setattr__(self, "_pykrx_ohlcv_available", True)
                            return df
                    except Exception as exc:
                        object.__setattr__(self, "_pykrx_ohlcv_available", False)
                        logger.warning("pykrx OHLCV disabled for this run after failure: %s", exc)
                        return self._fallback_ohlcv(
                            ticker=ticker,
                            start_date=start_date,
                            end_date=end_date,
                            adjusted=adjusted,
                        )
                pykrx_available = object.__getattribute__(self, "_pykrx_ohlcv_available")

        if pykrx_available is False:
            return self._fallback_ohlcv(ticker=ticker, start_date=start_date, end_date=end_date, adjusted=adjusted)

        try:
            df = pykrx_provider.fetch_ohlcv(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
                adjusted=adjusted,
            )
            if df is not None and not df.empty:
                return df
        except Exception as exc:
            object.__setattr__(self, "_pykrx_ohlcv_available", False)
            logger.warning("pykrx OHLCV disabled for this run after failure: %s", exc)

        return self._fallback_ohlcv(ticker=ticker, start_date=start_date, end_date=end_date, adjusted=adjusted)

    def fetch_market_cap(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        pykrx_provider = object.__getattribute__(self, "_pykrx_provider")
        lock = object.__getattribute__(self, "_pykrx_lock")
        pykrx_available = object.__getattribute__(self, "_pykrx_market_cap_available")

        if not self._is_pykrx_compatible_ticker(ticker):
            return self._fallback_market_cap()

        if pykrx_available is False:
            return self._fallback_market_cap()

        if pykrx_available is None:
            with lock:
                pykrx_available = object.__getattribute__(self, "_pykrx_market_cap_available")
                if pykrx_available is None:
                    try:
                        df = pykrx_provider.fetch_market_cap(
                            ticker=ticker,
                            start_date=start_date,
                            end_date=end_date,
                        )
                        if df is not None and not df.empty:
                            object.__setattr__(self, "_pykrx_market_cap_available", True)
                            return df
                    except Exception as exc:
                        object.__setattr__(self, "_pykrx_market_cap_available", False)
                        logger.warning("pykrx market cap disabled for this run after failure: %s", exc)
                        return self._fallback_market_cap()
                pykrx_available = object.__getattribute__(self, "_pykrx_market_cap_available")

        if pykrx_available is False:
            return self._fallback_market_cap()

        try:
            df = pykrx_provider.fetch_market_cap(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
            )
            if df is not None and not df.empty:
                return df
        except Exception as exc:
            object.__setattr__(self, "_pykrx_market_cap_available", False)
            logger.warning("pykrx market cap disabled for this run after failure: %s", exc)

        return self._fallback_market_cap()

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

    def fetch_ohlcv_bulk(
        self,
        *,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """Bulk-fetch OHLCV for all tickers via pykrx date-level API.

        Called once before concurrent per-ticker processing to eliminate
        per-ticker pykrx HTTP calls and their thread-safety issues.
        Returns an empty DataFrame if pykrx is unavailable.
        """
        pykrx_provider = object.__getattribute__(self, "_pykrx_provider")
        lock = object.__getattribute__(self, "_pykrx_lock")
        with lock:
            try:
                df = pykrx_provider.fetch_ohlcv_bulk(
                    start_date=start_date,
                    end_date=end_date,
                    adjusted=adjusted,
                )
                if df is not None and not df.empty:
                    object.__setattr__(self, "_pykrx_ohlcv_available", True)
                    return df
            except Exception as exc:
                object.__setattr__(self, "_pykrx_ohlcv_available", False)
                logger.warning("pykrx bulk OHLCV failed: %s", exc)
        return pd.DataFrame()

    def fetch_market_cap_bulk(
        self,
        *,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Bulk-fetch market cap for all tickers via pykrx date-level API.

        Returns an empty DataFrame if pykrx is unavailable.
        """
        pykrx_provider = object.__getattribute__(self, "_pykrx_provider")
        lock = object.__getattribute__(self, "_pykrx_lock")
        with lock:
            try:
                df = pykrx_provider.fetch_market_cap_bulk(
                    start_date=start_date,
                    end_date=end_date,
                )
                if df is not None and not df.empty:
                    object.__setattr__(self, "_pykrx_market_cap_available", True)
                    return df
            except Exception as exc:
                object.__setattr__(self, "_pykrx_market_cap_available", False)
                logger.warning("pykrx bulk market cap failed: %s", exc)
        return pd.DataFrame()

    def disable_pykrx_per_ticker(self) -> None:
        """Prevent concurrent per-ticker pykrx calls after a successful bulk prefetch.

        pykrx is not thread-safe; bulk data covers all standard KRX tickers so
        per-ticker pykrx calls are no longer needed. Tickers absent from the bulk
        result fall back to FDR.
        """
        object.__setattr__(self, "_pykrx_ohlcv_available", False)
        object.__setattr__(self, "_pykrx_market_cap_available", False)
