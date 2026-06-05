from __future__ import annotations

import datetime as dt
import hashlib
import logging
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd

from capybara_fetcher.providers import CompositeProvider
from capybara_fetcher.standardize import standardize_market_cap, standardize_ohlcv

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectionConfig:
    start_date: str
    end_date: str
    test_limit: int = 0
    max_workers: int = 4
    adjusted: bool = True
    market: str | None = None
    master_json_path: str | None = None


@dataclass(frozen=True)
class CollectionResult:
    industry_df: pd.DataFrame
    master_df: pd.DataFrame
    price_df: pd.DataFrame
    quality_metrics: dict[str, Any]


def _industry_code(large: object, mid: object, small: object) -> str:
    parts = [str(x).strip() if x is not None and pd.notna(x) else "" for x in [large, mid, small]]
    key = "|".join(parts)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest().upper()
    return digest[:10]


def _build_industry_df(master_raw: pd.DataFrame) -> pd.DataFrame:
    base = master_raw[["IndustryLarge", "IndustryMid", "IndustrySmall"]].copy()
    base = base.fillna("")
    base["INDUSTRY_CODE"] = base.apply(
        lambda r: _industry_code(r["IndustryLarge"], r["IndustryMid"], r["IndustrySmall"]),
        axis=1,
    )
    out = (
        base.rename(
            columns={
                "IndustryLarge": "LARGE_CLASS",
                "IndustryMid": "MEDIUM_CLASS",
                "IndustrySmall": "SMALL_CLASS",
            }
        )[["INDUSTRY_CODE", "LARGE_CLASS", "MEDIUM_CLASS", "SMALL_CLASS"]]
        .drop_duplicates()
        .sort_values(["LARGE_CLASS", "MEDIUM_CLASS", "SMALL_CLASS"])
        .reset_index(drop=True)
    )
    return out


def _asset_type(market: str) -> str:
    m = str(market).strip().upper()
    if m == "ETF":
        return "E"
    if m == "ETN":
        return "N"
    return "S"


def _build_master_df(master_raw: pd.DataFrame) -> pd.DataFrame:
    base = master_raw.copy()
    base["INDUSTRY_CODE"] = base.apply(
        lambda r: _industry_code(r.get("IndustryLarge"), r.get("IndustryMid"), r.get("IndustrySmall")),
        axis=1,
    )
    base["MARKET_CODE"] = base["Market"].astype(str).str.strip().str.upper()
    base["ASSET_TYPE"] = base["Market"].apply(_asset_type)
    base["IS_LISTED"] = "Y"
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    base["UPDATED_AT"] = now

    out = base.rename(columns={"Code": "TICKER", "Name": "STOCK_NAME"})[
        ["TICKER", "STOCK_NAME", "MARKET_CODE", "ASSET_TYPE", "INDUSTRY_CODE", "IS_LISTED", "UPDATED_AT"]
    ].copy()
    out["TICKER"] = out["TICKER"].astype(str).str.zfill(6)
    out = out.drop_duplicates(subset=["TICKER"]).sort_values("TICKER").reset_index(drop=True)
    return out


def _build_price_df(std_df: pd.DataFrame, *, master_raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = std_df.rename(
        columns={
            "Date": "PRICE_DATE",
            "Open": "OPEN_PRICE",
            "High": "HIGH_PRICE",
            "Low": "LOW_PRICE",
            "Close": "CLOSE_PRICE",
            "Volume": "VOLUME",
            "MarketCap": "MARKET_CAP",
            "Ticker": "TICKER",
        }
    ).copy()
    out["ADJ_CLOSE"] = out["CLOSE_PRICE"]

    out["MARKET_CAP"] = pd.to_numeric(out["MARKET_CAP"], errors="coerce")
    missing_before = int(out["MARKET_CAP"].isna().sum())

    shares_map = (
        master_raw.assign(Code=master_raw["Code"].astype(str).str.zfill(6))
        .drop_duplicates(subset=["Code"])
        .set_index("Code")["SharesOutstanding"]
    )
    out["SHARES_OUTSTANDING"] = pd.to_numeric(out["TICKER"].map(shares_map), errors="coerce")
    computed_cap = out["CLOSE_PRICE"] * out["SHARES_OUTSTANDING"]
    out["MARKET_CAP"] = out["MARKET_CAP"].combine_first(computed_cap)

    missing_after_enrichment = int(out["MARKET_CAP"].isna().sum())
    out["MARKET_CAP"] = out["MARKET_CAP"].fillna(0)
    zero_final = int((out["MARKET_CAP"] == 0).sum())

    out = out[
        [
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
    ]
    out = out.dropna(subset=["TICKER", "PRICE_DATE", "CLOSE_PRICE"]).sort_values(["TICKER", "PRICE_DATE"]).reset_index(drop=True)
    metrics = {
        "market_cap_missing_before": missing_before,
        "market_cap_missing_after_enrichment": missing_after_enrichment,
        "market_cap_zero_final": zero_final,
        "price_row_count": int(len(out)),
    }
    return out, metrics


def collect_data(cfg: CollectionConfig) -> CollectionResult:
    provider = CompositeProvider(master_json_path=cfg.master_json_path)

    master_raw = provider.load_stock_master()
    master_codes = set(master_raw["Code"].astype(str).str.zfill(6).tolist())
    tickers, _market_map = provider.list_tickers(market=cfg.market)
    tickers = [t for t in tickers if t in master_codes]
    if cfg.test_limit > 0:
        tickers = tickers[: cfg.test_limit]

    if not tickers:
        raise ValueError("no tickers available for collection")

    def fetch_one(ticker: str) -> pd.DataFrame:
        raw = provider.fetch_ohlcv(
            ticker=ticker,
            start_date=cfg.start_date,
            end_date=cfg.end_date,
            adjusted=cfg.adjusted,
        )
        std = standardize_ohlcv(raw, ticker=ticker)

        cap_raw = provider.fetch_market_cap(
            ticker=ticker,
            start_date=cfg.start_date,
            end_date=cfg.end_date,
        )
        cap_std = standardize_market_cap(cap_raw)
        if not cap_std.empty:
            std = std.merge(cap_std, on="Date", how="left", suffixes=("", "_from_cap"))
            if "MarketCap_from_cap" in std.columns:
                std["MarketCap"] = std["MarketCap"].combine_first(std["MarketCap_from_cap"])
                std = std.drop(columns=["MarketCap_from_cap"])

        # Secondary fallback: KIS snapshot market cap applied only where still missing.
        if std["MarketCap"].isna().any():
            snapshot = provider.fetch_market_cap_snapshot(ticker=ticker)
            if snapshot is not None and snapshot > 0:
                std["MarketCap"] = std["MarketCap"].fillna(snapshot)
        return std

    frames: list[pd.DataFrame] = []
    if cfg.max_workers <= 1:
        for t in tickers:
            one = fetch_one(t)
            if not one.empty:
                frames.append(one)
    else:
        with ThreadPoolExecutor(max_workers=cfg.max_workers) as ex:
            fut_map = {ex.submit(fetch_one, t): t for t in tickers}
            for fut in as_completed(fut_map):
                one = fut.result()
                if not one.empty:
                    frames.append(one)

    price_std = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["Date", "Ticker", "Open", "High", "Low", "Close", "Volume", "MarketCap"]
    )

    industry_df = _build_industry_df(master_raw)
    master_df = _build_master_df(master_raw)
    price_df, quality_metrics = _build_price_df(price_std, master_raw=master_raw)

    logger.info("Collected industry_df rows=%s cols=%s", len(industry_df), list(industry_df.columns))
    logger.info("Collected master_df rows=%s cols=%s", len(master_df), list(master_df.columns))
    logger.info("Collected price_df rows=%s cols=%s", len(price_df), list(price_df.columns))
    logger.info("Quality metrics: %s", quality_metrics)

    return CollectionResult(
        industry_df=industry_df,
        master_df=master_df,
        price_df=price_df,
        quality_metrics=quality_metrics,
    )
