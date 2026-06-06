from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import FinanceDataReader as fdr
import pandas as pd


@dataclass(frozen=True)
class FdrProvider:
    name: str = "fdr"
    source: str = "KRX"

    def list_tickers(
        self,
        *,
        asof_date: dt.date | None = None,
        market: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        _ = asof_date
        df_list: list[pd.DataFrame] = []

        fetch_kospi = market is None or market == "KOSPI"
        fetch_kosdaq = market is None or market == "KOSDAQ"
        fetch_etf = market is None or market == "ETF"

        if fetch_kospi:
            df_k = fdr.StockListing("KOSPI")
            if not df_k.empty:
                df_list.append(df_k)
        if fetch_kosdaq:
            df_q = fdr.StockListing("KOSDAQ")
            if not df_q.empty:
                df_list.append(df_q)
        if fetch_etf:
            df_e = fdr.StockListing("ETF/KR")
            if not df_e.empty:
                df_e = df_e.rename(columns={"Symbol": "Code"})
                df_e["Market"] = "ETF"
                df_list.append(df_e)

        if not df_list:
            return [], {}

        master = pd.concat(df_list, ignore_index=True)
        if "Code" not in master.columns:
            return [], {}

        if market:
            m = str(market).strip()
            master = master[master["Market"] == m]

        tickers = sorted(master["Code"].astype(str).str.zfill(6).unique().tolist())
        codes = master["Code"].astype(str).str.zfill(6).tolist()
        market_by_ticker = dict(zip(codes, master["Market"].tolist()))
        return tickers, market_by_ticker

    def fetch_ohlcv(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        _ = adjusted
        ticker_code = str(ticker).zfill(6)
        if self.source.upper() == "KRX":
            symbol = f"KRX:{ticker_code}"
            fallback_symbol = f"NAVER:{ticker_code}"
        elif self.source.upper() == "NAVER":
            symbol = f"NAVER:{ticker_code}"
            fallback_symbol = None
        elif self.source.upper() == "YAHOO":
            symbol = f"NAVER:{ticker_code}"
            fallback_symbol = None
        else:
            symbol = ticker_code
            fallback_symbol = None

        try:
            df = fdr.DataReader(symbol)
        except ValueError as e:
            if fallback_symbol and "is not supported" in str(e):
                df = fdr.DataReader(fallback_symbol)
            else:
                raise

        if df is None or df.empty:
            return pd.DataFrame()

        column_mapping = {
            "Open": "시가",
            "High": "고가",
            "Low": "저가",
            "Close": "종가",
            "Volume": "거래량",
            "Change": "등락률",
        }
        rename_dict = {k: v for k, v in column_mapping.items() if k in df.columns}
        df = df.rename(columns=rename_dict)

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        df = df.sort_index()
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        return df[(df.index >= start_dt) & (df.index <= end_dt)]

    def load_stock_master(self, *, asof_date: dt.date | None = None) -> pd.DataFrame:
        _ = asof_date
        raise NotImplementedError("FdrProvider does not provide stock master in this architecture")
