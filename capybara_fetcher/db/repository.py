from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any, Iterator

import pandas as pd

from .oracle_client import OracleClient
from .sql_templates import (
    DAILY_PRICE_MERGE,
    DAILY_PRICE_RS_ONLY_MERGE,
    STOCK_DIVIDEND_MERGE,
    STOCK_INDUSTRY_MERGE,
    STOCK_MASTER_MERGE,
)


logger = logging.getLogger(__name__)


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

    @staticmethod
    def _df_chunks(df: pd.DataFrame, *, chunk_rows: int) -> Iterator[pd.DataFrame]:
        if df is None or df.empty:
            return
        size = max(1, int(chunk_rows))
        for i in range(0, len(df), size):
            yield df.iloc[i : i + size]

    @staticmethod
    def _chunk_total(row_count: int, chunk_rows: int) -> int:
        if row_count <= 0:
            return 0
        return (int(row_count) + int(chunk_rows) - 1) // int(chunk_rows)

    def upsert_stock_industry(self, industry_df: pd.DataFrame) -> int:
        if industry_df is None or industry_df.empty:
            return 0

        total = 0
        chunk_rows = 20000
        base = industry_df[["INDUSTRY_CODE", "LARGE_CLASS", "MEDIUM_CLASS", "SMALL_CLASS"]].copy()
        for col in ["LARGE_CLASS", "MEDIUM_CLASS", "SMALL_CLASS"]:
            base[col] = base[col].fillna("UNKNOWN").astype(str).str.strip()
            base.loc[base[col] == "", col] = "UNKNOWN"

        total_chunks = self._chunk_total(len(base), chunk_rows)
        for idx, chunk in enumerate(self._df_chunks(base, chunk_rows=chunk_rows), start=1):
            rows = chunk.to_dict(orient="records")
            total += self._client.execute_many(STOCK_INDUSTRY_MERGE, rows)
            if total_chunks <= 10 or idx % 10 == 0 or idx == total_chunks:
                logger.info("STOCK_INDUSTRY upsert progress: chunk %s/%s rows=%s", idx, total_chunks, total)
        return total

    def upsert_stock_master(self, master_df: pd.DataFrame) -> int:
        if master_df is None or master_df.empty:
            return 0

        total = 0
        chunk_rows = 20000
        cols = ["TICKER", "STOCK_NAME", "MARKET_CODE", "ASSET_TYPE", "INDUSTRY_CODE", "IS_LISTED", "UPDATED_AT"]
        scoped = master_df[cols]
        total_chunks = self._chunk_total(len(scoped), chunk_rows)
        for idx, chunk in enumerate(self._df_chunks(scoped, chunk_rows=chunk_rows), start=1):
            rows: list[dict[str, Any]] = []
            for rec in chunk.to_dict(orient="records"):
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
            total += self._client.execute_many(STOCK_MASTER_MERGE, rows)
            if total_chunks <= 10 or idx % 10 == 0 or idx == total_chunks:
                logger.info("STOCK_MASTER upsert progress: chunk %s/%s rows=%s", idx, total_chunks, total)
        return total

    def upsert_daily_price(self, price_df: pd.DataFrame) -> int:
        if price_df is None or price_df.empty:
            return 0

        total = 0
        chunk_rows = 50000
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
            "RS_1M",
            "RS_3M",
            "RS_6M",
            "RS_12M",
            "RS_WEIGHTED",
        ]
        scoped = price_df[cols]
        total_chunks = self._chunk_total(len(scoped), chunk_rows)
        for idx, chunk in enumerate(self._df_chunks(scoped, chunk_rows=chunk_rows), start=1):
            rows: list[dict[str, Any]] = []
            for rec in chunk.to_dict(orient="records"):
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
                        "RS_1M": self._to_float(rec["RS_1M"]),
                        "RS_3M": self._to_float(rec["RS_3M"]),
                        "RS_6M": self._to_float(rec["RS_6M"]),
                        "RS_12M": self._to_float(rec["RS_12M"]),
                        "RS_WEIGHTED": self._to_float(rec["RS_WEIGHTED"]),
                    }
                )
            total += self._client.execute_many(DAILY_PRICE_MERGE, rows)
            if total_chunks <= 10 or idx % 10 == 0 or idx == total_chunks:
                logger.info("DAILY_PRICE upsert progress: chunk %s/%s rows=%s", idx, total_chunks, total)
        return total

    def upsert_daily_price_rs(self, price_df: pd.DataFrame) -> int:
        if price_df is None or price_df.empty:
            return 0

        total = 0
        chunk_rows = 50000
        cols = [
            "TICKER",
            "PRICE_DATE",
            "RS_1M",
            "RS_3M",
            "RS_6M",
            "RS_12M",
            "RS_WEIGHTED",
        ]
        scoped = price_df[cols]
        total_chunks = self._chunk_total(len(scoped), chunk_rows)
        for idx, chunk in enumerate(self._df_chunks(scoped, chunk_rows=chunk_rows), start=1):
            rows: list[dict[str, Any]] = []
            for rec in chunk.to_dict(orient="records"):
                rows.append(
                    {
                        "TICKER": str(rec["TICKER"]).zfill(6),
                        "PRICE_DATE": self._to_datetime(rec["PRICE_DATE"]),
                        "RS_1M": self._to_float(rec["RS_1M"]),
                        "RS_3M": self._to_float(rec["RS_3M"]),
                        "RS_6M": self._to_float(rec["RS_6M"]),
                        "RS_12M": self._to_float(rec["RS_12M"]),
                        "RS_WEIGHTED": self._to_float(rec["RS_WEIGHTED"]),
                    }
                )
            total += self._client.execute_many(DAILY_PRICE_RS_ONLY_MERGE, rows)
            if total_chunks <= 10 or idx % 10 == 0 or idx == total_chunks:
                logger.info("DAILY_PRICE RS-only upsert progress: chunk %s/%s rows=%s", idx, total_chunks, total)
        return total

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
        total = 0
        chunk_rows = 50000
        scoped = dividend_df[cols]
        total_chunks = self._chunk_total(len(scoped), chunk_rows)
        for idx, chunk in enumerate(self._df_chunks(scoped, chunk_rows=chunk_rows), start=1):
            rows: list[dict[str, Any]] = []
            for rec in chunk.to_dict(orient="records"):
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
            total += self._client.execute_many(STOCK_DIVIDEND_MERGE, rows)
            if total_chunks <= 10 or idx % 10 == 0 or idx == total_chunks:
                logger.info("STOCK_DIVIDEND upsert progress: chunk %s/%s rows=%s", idx, total_chunks, total)
        return total

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
