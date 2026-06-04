"""
Composite data provider.

This provider combines multiple data providers to provide unified access.
The selection strategy for which provider to use for each operation
is implemented in the method bodies.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..provider import DataProvider
from .pykrx_provider import PykrxProvider
from .korea_investment_provider import KoreaInvestmentProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompositeProvider(DataProvider):
    """
    DataProvider implementation that combines multiple providers.
    
    This provider delegates operations to one or more underlying providers
    based on a selection strategy (to be implemented).
    
    The internal provider selection and combination logic is hidden from external users.
    
    Attributes:
        name: Provider identifier (default: "composite")
    """

    name: str = "composite"
    _pykrx_provider: DataProvider = field(default=None, init=False, repr=False, compare=False)
    _korea_investment_provider: DataProvider = field(default=None, init=False, repr=False, compare=False)
    
    def __post_init__(self):
        """Initialize internal providers."""
        # Initialize PykrxProvider for OHLCV operations
        master_json_path = self._get_master_json_path()
        pykrx_provider = PykrxProvider(master_json_path=master_json_path)
        object.__setattr__(self, "_pykrx_provider", pykrx_provider)
        
        # Initialize KoreaInvestmentProvider for list_tickers operation
        appkey = os.environ.get("HT_KE", "")
        appsecret = os.environ.get("HT_SE", "")
        korea_investment_provider = KoreaInvestmentProvider(
            master_json_path=master_json_path,
            appkey=appkey,
            appsecret=appsecret,
        )
        object.__setattr__(self, "_korea_investment_provider", korea_investment_provider)
        
        # Log which provider is used for each operation (once at initialization)
        logger.info(
            f"CompositeProvider initialized: list_tickers -> '{korea_investment_provider.name}', "
            f"load_stock_master -> 'composite', fetch_ohlcv -> '{pykrx_provider.name}'"
        )
    
    def _get_master_json_path(self) -> str:
        """Get the path to master JSON file."""
        # Try to find master JSON in common locations
        package_dir = Path(__file__).parent.parent.parent
        possible_paths = [
            package_dir / "data" / "krx_stock_master.json",
            Path("data") / "krx_stock_master.json",
            Path("/workspace/data/krx_stock_master.json"),  # For CI/CD environments
        ]
        
        for path in possible_paths:
            if path.exists():
                return str(path)
        
        # If not found, raise an error
        raise FileNotFoundError(
            f"Could not find krx_stock_master.json. Tried: {[str(p) for p in possible_paths]}"
        )

    def list_tickers(
        self,
        *,
        asof_date: dt.date | None = None,
        market: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        """
        List tickers from the composite provider.
        
        Delegates to the internal KoreaInvestmentProvider,
        same as KoreaInvestmentProvider.list_tickers.
        
        Returns:
          - tickers: list of 6-digit strings (sorted)
          - market_by_ticker: mapping ticker -> market label (if known)
        """
        korea_investment_provider = object.__getattribute__(self, "_korea_investment_provider")
        return korea_investment_provider.list_tickers(asof_date=asof_date, market=market)

    def load_stock_master(
        self,
        *,
        asof_date: dt.date | None = None,
    ) -> pd.DataFrame:
        """
        Load stock master from the composite provider.
        
        Loads stock master data from local JSON file which includes KOSPI, KOSDAQ, and ETF data.
        
        Returns a DataFrame that includes at least:
          Code, Name, Market, IndustryLarge, IndustryMid, IndustrySmall, SharesOutstanding
        """
        # asof_date reserved for future providers
        
        # Load master data from JSON file (same logic as provider_utils.load_master_json)
        master_json_path = self._get_master_json_path()
        
        _MASTER_COLS = [
            "Code",
            "Name",
            "Market",
            "IndustryLarge",
            "IndustryMid",
            "IndustrySmall",
            "SharesOutstanding",
        ]
        
        # Load master data from JSON (includes KOSPI, KOSDAQ, and ETF)
        with open(master_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        df = pd.DataFrame(data)
        if df.empty:
            raise ValueError(f"stock master is empty: {master_json_path}")
        
        for c in _MASTER_COLS:
            if c not in df.columns:
                df[c] = pd.NA
        
        master = df[_MASTER_COLS].copy()
        master["Code"] = master["Code"].astype(str).str.strip().str.zfill(6)
        master["Name"] = master["Name"].astype(str).str.strip()
        master["Market"] = master["Market"].astype(str).str.strip()
        
        # Handle industry columns carefully - don't convert None to "None" string
        for col in ["IndustryLarge", "IndustryMid", "IndustrySmall"]:
            master[col] = master[col].apply(lambda x: str(x).strip() if pd.notna(x) and x is not None else pd.NA)
        
        master["SharesOutstanding"] = pd.to_numeric(master["SharesOutstanding"], errors="coerce").astype("Int64")
        master = master.dropna(subset=["Code"]).drop_duplicates(subset=["Code", "Market"]).sort_values(["Market", "Code"])
        
        if master.empty:
            raise ValueError(f"stock master has no valid rows: {master_json_path}")
        
        return master

    def fetch_ohlcv(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV for a ticker and date range from the composite provider.
        
        Currently delegates to the internal PykrxProvider,
        same as PykrxProvider.fetch_ohlcv.
        
        Returns DataFrame with DatetimeIndex and Korean column names (same as pykrx).
        The returned shape is provider-specific (raw).
        Standardization is handled elsewhere.
        """
        pykrx_provider = object.__getattribute__(self, "_pykrx_provider")
        return pykrx_provider.fetch_ohlcv(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            adjusted=adjusted,
        )

