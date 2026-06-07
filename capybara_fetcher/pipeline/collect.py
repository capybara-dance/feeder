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
    dividend_df: pd.DataFrame
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


def _build_dividend_df(std_df: pd.DataFrame) -> pd.DataFrame:
    if std_df is None or std_df.empty:
        return pd.DataFrame(
            columns=[
                "TICKER",
                "EX_DIVIDEND_DATE",
                "DIVIDEND_PER_SHARE",
                "RECORD_DATE",
                "PAYMENT_DATE",
                "DIVIDEND_TYPE",
            ]
        )

    out = std_df.rename(
        columns={
            "Date": "EX_DIVIDEND_DATE",
            "Ticker": "TICKER",
            "Dividend": "DIVIDEND_PER_SHARE",
        }
    ).copy()
    out["TICKER"] = out["TICKER"].astype(str).str.zfill(6)
    out["EX_DIVIDEND_DATE"] = pd.to_datetime(out["EX_DIVIDEND_DATE"], errors="coerce").dt.normalize()
    out["DIVIDEND_PER_SHARE"] = pd.to_numeric(out["DIVIDEND_PER_SHARE"], errors="coerce")
    out["RECORD_DATE"] = pd.NaT
    out["PAYMENT_DATE"] = pd.NaT
    out["DIVIDEND_TYPE"] = "R"
    out = out[
        [
            "TICKER",
            "EX_DIVIDEND_DATE",
            "DIVIDEND_PER_SHARE",
            "RECORD_DATE",
            "PAYMENT_DATE",
            "DIVIDEND_TYPE",
        ]
    ]
    out = out.dropna(subset=["TICKER", "EX_DIVIDEND_DATE", "DIVIDEND_PER_SHARE"])
    out = out.drop_duplicates(subset=["TICKER", "EX_DIVIDEND_DATE"], keep="first")
    out = out.sort_values(["TICKER", "EX_DIVIDEND_DATE"]).reset_index(drop=True)
    return out


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

    # Bulk-fetch all pykrx data upfront (single-threaded) before spawning workers.
    # pykrx is not thread-safe: concurrent per-ticker calls from multiple workers
    # cause race conditions in its shared HTTP session.  One bulk call per date
    # (covering all KRX tickers at once) is both faster and safe.
    bulk_ohlcv_map: dict[str, pd.DataFrame] = {}
    bulk_cap_map: dict[str, pd.DataFrame] = {}

    bulk_ohlcv = provider.fetch_ohlcv_bulk(
        start_date=cfg.start_date,
        end_date=cfg.end_date,
        adjusted=cfg.adjusted,
    )
    if not bulk_ohlcv.empty and "Ticker" in bulk_ohlcv.columns:
        bulk_ohlcv["Ticker"] = bulk_ohlcv["Ticker"].astype(str).str.zfill(6)
        bulk_ohlcv_map = {t: grp.reset_index(drop=True) for t, grp in bulk_ohlcv.groupby("Ticker")}
        logger.info("pykrx bulk OHLCV: %s rows, %s tickers", len(bulk_ohlcv), len(bulk_ohlcv_map))
        # Disable per-ticker pykrx to prevent thread-safety issues in the fallback path.
        provider.disable_pykrx_per_ticker()
    else:
        logger.info("pykrx bulk OHLCV unavailable; will use per-ticker fallback")

    bulk_cap = provider.fetch_market_cap_bulk(
        start_date=cfg.start_date,
        end_date=cfg.end_date,
    )
    if not bulk_cap.empty and "Ticker" in bulk_cap.columns:
        bulk_cap["Ticker"] = bulk_cap["Ticker"].astype(str).str.zfill(6)
        bulk_cap_map = {t: grp.reset_index(drop=True) for t, grp in bulk_cap.groupby("Ticker")}
        logger.info("pykrx bulk market cap: %s rows, %s tickers", len(bulk_cap), len(bulk_cap_map))

    def fetch_one(ticker: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        ticker_padded = str(ticker).zfill(6)

        # OHLCV: use pre-fetched bulk data when available, otherwise fall back to
        # per-ticker provider (which will use FDR since pykrx is disabled above).
        if ticker_padded in bulk_ohlcv_map:
            std = standardize_ohlcv(bulk_ohlcv_map[ticker_padded], ticker=ticker)
        else:
            raw = provider.fetch_ohlcv(
                ticker=ticker,
                start_date=cfg.start_date,
                end_date=cfg.end_date,
                adjusted=cfg.adjusted,
            )
            std = standardize_ohlcv(raw, ticker=ticker)

        # Market cap: use pre-fetched bulk data when available.
        if ticker_padded in bulk_cap_map:
            cap_std = standardize_market_cap(bulk_cap_map[ticker_padded])
        else:
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
        div_raw = provider.fetch_dividends(
            ticker=ticker,
            start_date=cfg.start_date,
            end_date=cfg.end_date,
        )
        div_std = div_raw.copy()
        if not div_std.empty:
            if "Date" in div_std.columns:
                div_std["Date"] = pd.to_datetime(div_std["Date"], errors="coerce").dt.normalize()
            if "Dividend" in div_std.columns:
                div_std["Dividend"] = pd.to_numeric(div_std["Dividend"], errors="coerce")
            div_std["Ticker"] = str(ticker).zfill(6)
            div_std = div_std[["Date", "Ticker", "Dividend"]].dropna(subset=["Date", "Ticker", "Dividend"])
        else:
            div_std = pd.DataFrame(columns=["Date", "Ticker", "Dividend"])

        return std, div_std

    frames: list[pd.DataFrame] = []
    dividend_frames: list[pd.DataFrame] = []
    if cfg.max_workers <= 1:
        for t in tickers:
            price_one, div_one = fetch_one(t)
            if not price_one.empty:
                frames.append(price_one)
            if not div_one.empty:
                dividend_frames.append(div_one)
    else:
        with ThreadPoolExecutor(max_workers=cfg.max_workers) as ex:
            fut_map = {ex.submit(fetch_one, t): t for t in tickers}
            for fut in as_completed(fut_map):
                price_one, div_one = fut.result()
                if not price_one.empty:
                    frames.append(price_one)
                if not div_one.empty:
                    dividend_frames.append(div_one)

    price_std = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["Date", "Ticker", "Open", "High", "Low", "Close", "Volume", "MarketCap"]
    )

    industry_df = _build_industry_df(master_raw)
    master_df = _build_master_df(master_raw)
    price_df, quality_metrics = _build_price_df(price_std, master_raw=master_raw)
    dividend_std = pd.concat(dividend_frames, ignore_index=True) if dividend_frames else pd.DataFrame(
        columns=["Date", "Ticker", "Dividend"]
    )
    dividend_df = _build_dividend_df(dividend_std)
    quality_metrics["dividend_row_count"] = int(len(dividend_df))

    logger.info("Collected industry_df rows=%s cols=%s", len(industry_df), list(industry_df.columns))
    logger.info("Collected master_df rows=%s cols=%s", len(master_df), list(master_df.columns))
    logger.info("Collected price_df rows=%s cols=%s", len(price_df), list(price_df.columns))
    logger.info("Collected dividend_df rows=%s cols=%s", len(dividend_df), list(dividend_df.columns))
    logger.info("Quality metrics: %s", quality_metrics)

    return CollectionResult(
        industry_df=industry_df,
        master_df=master_df,
        price_df=price_df,
        dividend_df=dividend_df,
        quality_metrics=quality_metrics,
    )
