"""
FinanceDataReader (FDR) data provider.

This provider uses FinanceDataReader library to fetch stock data:
https://github.com/FinanceData/FinanceDataReader
"""
from __future__ import annotations

import datetime as dt
import warnings
from dataclasses import dataclass

import pandas as pd
import FinanceDataReader as fdr

from ..provider import DataProvider
from .provider_utils import load_master_json


@dataclass(frozen=True)
class FdrProvider(DataProvider):
    """
    DataProvider implementation using FinanceDataReader:
    - tickers: fetched dynamically via fdr.StockListing() for KOSPI, KOSDAQ, and ETF/KR
    - ohlcv: FinanceDataReader (FDR) library
    
    FDR supports multiple data sources:
    - KRX (Korean Exchange) - default for Korean stocks
    - NAVER Finance
    - Yahoo Finance
    
    The provider uses KRX as the default source for Korean stock data.
    Note: list_tickers() requires network access to fetch current ticker lists.
    """

    master_json_path: str
    source: str = "KRX"  # Data source: "KRX", "NAVER", or "YAHOO"
    name: str = "fdr"

    def load_stock_master(self, *, asof_date: dt.date | None = None) -> pd.DataFrame:
        """Load stock master from local JSON file."""
        # asof_date reserved for future providers
        return load_master_json(self.master_json_path)

    def list_tickers(
        self,
        *,
        asof_date: dt.date | None = None,
        market: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        """
        List tickers using fdr.StockListing() for KOSPI, KOSDAQ, and ETF markets.
        
        This method fetches ticker data directly from FinanceDataReader instead of
        reading from a local JSON file.
        
        Args:
            asof_date: Not used. Current live data is always fetched.
            market: Optional market filter. If None, returns all markets (KOSPI, KOSDAQ, ETF).
                   If specified, returns only tickers from that market ('KOSPI', 'KOSDAQ', or 'ETF')
            
        Returns:
            Tuple of (ticker_list, market_by_ticker_dict)
        """
        # Fetch data from requested markets
        df_list = []
        
        # Determine which markets to fetch based on market parameter
        fetch_kospi = market is None or market == 'KOSPI'
        fetch_kosdaq = market is None or market == 'KOSDAQ'
        fetch_etf = market is None or market == 'ETF'
        
        # Fetch KOSPI data
        if fetch_kospi:
            try:
                df_kospi = fdr.StockListing('KOSPI')
                if not df_kospi.empty:
                    df_list.append(df_kospi)
            except Exception as e:
                warnings.warn(f"Failed to fetch KOSPI listings: {str(e)}")
        
        # Fetch KOSDAQ data
        if fetch_kosdaq:
            try:
                df_kosdaq = fdr.StockListing('KOSDAQ')
                if not df_kosdaq.empty:
                    df_list.append(df_kosdaq)
            except Exception as e:
                warnings.warn(f"Failed to fetch KOSDAQ listings: {str(e)}")
        
        # Fetch ETF/KR data
        if fetch_etf:
            try:
                df_etf = fdr.StockListing('ETF/KR')
                if not df_etf.empty:
                    # NaverEtfListing returns 'Symbol' instead of 'Code'
                    df_etf = df_etf.rename(columns={'Symbol': 'Code'})
                    # Add Market column for ETF
                    df_etf['Market'] = 'ETF'
                    df_list.append(df_etf)
            except Exception as e:
                warnings.warn(f"Failed to fetch ETF/KR listings: {str(e)}")
        
        # Combine all dataframes
        if not df_list:
            # If all fetches failed, return empty results
            return [], {}
        
        master = pd.concat(df_list, ignore_index=True)
        
        # Ensure Code column exists and is properly formatted
        if 'Code' not in master.columns:
            raise ValueError("Code column not found in fetched data")
        
        # Filter by exact market match if specified
        # This is needed because fdr.StockListing('KOSDAQ') returns both 'KOSDAQ' and 'KOSDAQ GLOBAL'
        if market:
            m = str(market).strip()
            master = master[master["Market"] == m]
        
        # Format ticker codes as 6-digit strings
        tickers = master["Code"].astype(str).str.zfill(6).unique().tolist()
        tickers = sorted(tickers)
        
        # Create market mapping dictionary
        ticker_codes = master["Code"].astype(str).str.zfill(6).tolist()
        market_by_ticker = dict(zip(ticker_codes, master["Market"].tolist()))
        
        return tickers, market_by_ticker

    def fetch_ohlcv(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data using FinanceDataReader.
        
        Args:
            ticker: 6-digit stock code
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            adjusted: Whether to use adjusted prices (default: True)
                     Note: FDR's KRX source provides adjusted prices by default
        
        Returns:
            DataFrame with Korean column names (matching pykrx format)
            for consistency with standardization layer.
        """
        ticker_code = str(ticker).zfill(6)
        
        # Build the symbol based on source
        # Note: KRX source doesn't support all tickers (e.g., ETFs like 069500)
        # We fetch all available data and then filter to the requested date range
        # We'll try KRX first, then fall back to NAVER if it fails
        if self.source.upper() == "KRX":
            symbol = f"KRX:{ticker_code}"
            fallback_symbol = f"NAVER:{ticker_code}"
        elif self.source.upper() == "NAVER":
            symbol = f"NAVER:{ticker_code}"
            fallback_symbol = None
        elif self.source.upper() == "YAHOO":
            # Yahoo Finance requires .KS (KOSPI) or .KQ (KOSDAQ) suffix
            # We'll default to NAVER for Yahoo to avoid market determination complexity
            # Users needing Yahoo should specify the full symbol externally
            symbol = f"NAVER:{ticker_code}"
            fallback_symbol = None
        else:
            # Default to ticker code without prefix (FDR will use NAVER)
            symbol = ticker_code
            fallback_symbol = None
        
        # Fetch all available data without specifying date range
        # This avoids API rate limits and threading issues
        try:
            df = fdr.DataReader(symbol)
        except ValueError as e:
            # KRX source may not support certain tickers (e.g., ETFs)
            # Fall back to NAVER if available
            if fallback_symbol and "is not supported" in str(e):
                try:
                    df = fdr.DataReader(fallback_symbol)
                except Exception as fallback_error:
                    raise RuntimeError(
                        f"Failed to fetch OHLCV from FDR for {ticker}: "
                        f"KRX source failed ({str(e)}), NAVER fallback also failed ({str(fallback_error)})"
                    ) from fallback_error
            else:
                raise RuntimeError(f"Failed to fetch OHLCV from FDR for {ticker} (source: {self.source}): {str(e)}") from e
        except Exception as e:
            raise RuntimeError(f"Failed to fetch OHLCV from FDR for {ticker} (source: {self.source}): {str(e)}") from e
        
        try:
            if df is None or df.empty:
                return pd.DataFrame()
            
            # FDR returns DataFrame with English column names
            # Common FDR columns: Date (index), Open, High, Low, Close, Volume, Change
            # Map to Korean column names (matching pykrx format)
            column_mapping = {
                "Open": "시가",
                "High": "고가",
                "Low": "저가",
                "Close": "종가",
                "Volume": "거래량",
                "Change": "등락률",
            }
            
            # Only rename columns that exist
            rename_dict = {k: v for k, v in column_mapping.items() if k in df.columns}
            df = df.rename(columns=rename_dict)
            
            # Ensure index is DatetimeIndex with name "날짜"
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            
            # Sort by date
            df = df.sort_index()
            
            # Filter to requested date range
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)
            df = df[(df.index >= start_dt) & (df.index <= end_dt)]
            
            # Add 거래대금 (trading value) if not present
            # Trading value approximation: Volume * Close
            # Note: This is an approximation as true trading value would be the sum of
            # (price * volume) for each individual trade throughout the day. Using
            # Volume * Close provides a reasonable estimate when intraday data is unavailable.
            if "거래대금" not in df.columns and "거래량" in df.columns and "종가" in df.columns:
                df["거래대금"] = df["거래량"] * df["종가"]
            
            return df
        except Exception as e:
            raise RuntimeError(f"Failed to process OHLCV data from FDR for {ticker}: {str(e)}") from e
