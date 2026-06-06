from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .oracle_client import OracleClient
from .sql_templates import (
    DAILY_PRICE_MERGE,
    STOCK_DIVIDEND_MERGE,
    STOCK_INDUSTRY_MERGE,
    STOCK_MASTER_MERGE,
)


@dataclass(frozen=True)
class UpsertSummary:
    stock_industry_rows: int = 0
    stock_master_rows: int = 0
    daily_price_rows: int = 0
    stock_dividend_rows: int = 0


class OracleRepository:
    def __init__(self, client: OracleClient) -> None:
        self._client = client

    @staticmethod
    def _to_datetime(value: Any) -> dt.datetime | None:
        if value is None or pd.isna(value):
            return None
        ts = pd.to_datetime(value, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime().replace(tzinfo=None)

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None or pd.isna(value):
            return None
        return float(value)

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value is None or pd.isna(value):
            return None
        return int(value)

    def upsert_stock_industry(self, industry_df: pd.DataFrame) -> int:
        if industry_df is None or industry_df.empty:
            return 0

        base = industry_df[["INDUSTRY_CODE", "LARGE_CLASS", "MEDIUM_CLASS", "SMALL_CLASS"]].copy()
        for col in ["LARGE_CLASS", "MEDIUM_CLASS", "SMALL_CLASS"]:
            base[col] = base[col].fillna("UNKNOWN").astype(str).str.strip()
            base.loc[base[col] == "", col] = "UNKNOWN"

        rows = base.to_dict(orient="records")
        return self._client.execute_many(STOCK_INDUSTRY_MERGE, rows)

    def upsert_stock_master(self, master_df: pd.DataFrame) -> int:
        if master_df is None or master_df.empty:
            return 0

        rows: list[dict[str, Any]] = []
        for rec in master_df[
            ["TICKER", "STOCK_NAME", "MARKET_CODE", "ASSET_TYPE", "INDUSTRY_CODE", "IS_LISTED", "UPDATED_AT"]
        ].to_dict(orient="records"):
            rows.append(
                {
                    "TICKER": str(rec["TICKER"]).zfill(6),
                    "STOCK_NAME": str(rec["STOCK_NAME"]),
                    "MARKET_CODE": str(rec["MARKET_CODE"]),
                    "ASSET_TYPE": str(rec["ASSET_TYPE"]),
                    "INDUSTRY_CODE": rec["INDUSTRY_CODE"],
                    "IS_LISTED": str(rec.get("IS_LISTED", "Y")),
                    "UPDATED_AT": self._to_datetime(rec.get("UPDATED_AT")) or dt.datetime.utcnow(),
                }
            )
        return self._client.execute_many(STOCK_MASTER_MERGE, rows)

    def upsert_daily_price(self, price_df: pd.DataFrame) -> int:
        if price_df is None or price_df.empty:
            return 0

        rows: list[dict[str, Any]] = []
        cols = [
            "TICKER",
            "PRICE_DATE",
            "OPEN_PRICE",
            "HIGH_PRICE",
            "LOW_PRICE",
            "CLOSE_PRICE",
            "ADJ_CLOSE",
            "VOLUME",
            "MARKET_CAP",
        ]
        for rec in price_df[cols].to_dict(orient="records"):
            rows.append(
                {
                    "TICKER": str(rec["TICKER"]).zfill(6),
                    "PRICE_DATE": self._to_datetime(rec["PRICE_DATE"]),
                    "OPEN_PRICE": self._to_float(rec["OPEN_PRICE"]),
                    "HIGH_PRICE": self._to_float(rec["HIGH_PRICE"]),
                    "LOW_PRICE": self._to_float(rec["LOW_PRICE"]),
                    "CLOSE_PRICE": self._to_float(rec["CLOSE_PRICE"]),
                    "ADJ_CLOSE": self._to_float(rec["ADJ_CLOSE"]),
                    "VOLUME": self._to_int(rec["VOLUME"]),
                    "MARKET_CAP": self._to_float(rec["MARKET_CAP"]),
                }
            )
        return self._client.execute_many(DAILY_PRICE_MERGE, rows)

    def upsert_stock_dividend(self, dividend_df: pd.DataFrame) -> int:
        if dividend_df is None or dividend_df.empty:
            return 0

        cols = [
            "TICKER",
            "EX_DIVIDEND_DATE",
            "DIVIDEND_PER_SHARE",
            "RECORD_DATE",
            "PAYMENT_DATE",
            "DIVIDEND_TYPE",
        ]
        rows: list[dict[str, Any]] = []
        for rec in dividend_df[cols].to_dict(orient="records"):
            rows.append(
                {
                    "TICKER": str(rec["TICKER"]).zfill(6),
                    "EX_DIVIDEND_DATE": self._to_datetime(rec["EX_DIVIDEND_DATE"]),
                    "DIVIDEND_PER_SHARE": self._to_float(rec["DIVIDEND_PER_SHARE"]),
                    "RECORD_DATE": self._to_datetime(rec["RECORD_DATE"]),
                    "PAYMENT_DATE": self._to_datetime(rec["PAYMENT_DATE"]),
                    "DIVIDEND_TYPE": str(rec.get("DIVIDEND_TYPE") or "R"),
                }
            )
        return self._client.execute_many(STOCK_DIVIDEND_MERGE, rows)

    def upsert_all(
        self,
        *,
        industry_df: pd.DataFrame,
        master_df: pd.DataFrame,
        price_df: pd.DataFrame,
        dividend_df: pd.DataFrame,
    ) -> UpsertSummary:
        return UpsertSummary(
            stock_industry_rows=self.upsert_stock_industry(industry_df),
            stock_master_rows=self.upsert_stock_master(master_df),
            daily_price_rows=self.upsert_daily_price(price_df),
            stock_dividend_rows=self.upsert_stock_dividend(dividend_df),
        )
