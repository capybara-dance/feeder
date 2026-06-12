from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

import pandas as pd
import pyarrow.parquet as pq

from .collect import CollectionResult, _build_industry_df, _build_master_df, _build_price_df


logger = logging.getLogger(__name__)


RS_COLUMN_ALIASES: dict[str, list[str]] = {
    "RS_1M": ["RS_1M", "rs_1m", "RS1M", "MRS_1M", "mrs_1m", "RS 1M", "rs 1m"],
    "RS_3M": ["RS_3M", "rs_3m", "RS3M", "MRS_3M", "mrs_3m", "RS 3M", "rs 3m"],
    "RS_6M": ["RS_6M", "rs_6m", "RS6M", "MRS_6M", "mrs_6m", "RS 6M", "rs 6m"],
    "RS_12M": ["RS_12M", "rs_12m", "RS12M", "MRS_12M", "mrs_12m", "RS 12M", "rs 12m"],
    "RS_WEIGHTED": [
        "RS_WEIGHTED",
        "rs_weighted",
        "WEIGHTED_RS",
        "weighted_rs",
        "Weighted RS",
        "weighted rs",
        "WeightedRS",
    ],
}


def _compute_weighted_rs(df: pd.DataFrame) -> pd.Series:
    # Weighted RS rule: 1m/3m/6m/12m with weights 4:3:2:1.
    weights = {
        "RS_1M": 4.0,
        "RS_3M": 3.0,
        "RS_6M": 2.0,
        "RS_12M": 1.0,
    }

    weighted_sum = pd.Series(0.0, index=df.index, dtype="float64")
    weight_sum = pd.Series(0.0, index=df.index, dtype="float64")
    for col, weight in weights.items():
        vals = pd.to_numeric(df[col], errors="coerce")
        valid = vals.notna()
        weighted_sum = weighted_sum + vals.fillna(0.0) * weight
        weight_sum = weight_sum + valid.astype("float64") * weight

    out = weighted_sum / weight_sum
    out = out.where(weight_sum > 0)
    return out


@dataclass(frozen=True)
class ReleaseInfo:
    repo: str
    tag: str
    name: str
    published_at: str | None


@dataclass(frozen=True)
class ReleaseCollection:
    result: CollectionResult
    release: ReleaseInfo


@dataclass
class ReleasePreparedData:
    release: ReleaseInfo
    temp_dir: str
    master_parquet_path: str
    feature_parquet_path: str
    industry_df: pd.DataFrame
    master_df: pd.DataFrame
    shares_map: pd.Series
    allowed_tickers: set[str]

    def cleanup(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)


def _resolve_col_case_insensitive(columns: list[str] | set[str], aliases: list[str]) -> str | None:
    by_lower = {str(c).strip().lower(): str(c) for c in columns}
    for alias in aliases:
        col = by_lower.get(alias.strip().lower())
        if col:
            return col
    return None


def _attach_release_rs_columns(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    out = frame.copy()
    missing: list[str] = []
    for target, aliases in RS_COLUMN_ALIASES.items():
        source_col = _resolve_col_case_insensitive(list(out.columns), aliases)
        if source_col is None:
            out[target] = pd.NA
            missing.append(target)
            continue
        out[target] = pd.to_numeric(out[source_col], errors="coerce")
    return out, missing


def _api_json(url: str, *, token: str | None = None) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "feeder-sync-oracle",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urlrequest.Request(url=url, headers=headers)
    try:
        with urlrequest.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"GitHub API request failed: {url} status={e.code} body={body[:400]}") from e


def _resolve_release(repo: str, *, tag: str | None, token: str | None) -> tuple[ReleaseInfo, dict[str, str]]:
    if tag:
        api = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    else:
        api = f"https://api.github.com/repos/{repo}/releases/latest"

    payload = _api_json(api, token=token)
    resolved_tag = str(payload.get("tag_name") or "")
    if not resolved_tag:
        raise RuntimeError("Could not resolve release tag from GitHub API response")

    assets = payload.get("assets") or []
    asset_map: dict[str, str] = {}
    for a in assets:
        name = str(a.get("name") or "").strip()
        download_url = str(a.get("browser_download_url") or "").strip()
        if name and download_url:
            asset_map[name] = download_url

    info = ReleaseInfo(
        repo=repo,
        tag=resolved_tag,
        name=str(payload.get("name") or resolved_tag),
        published_at=payload.get("published_at"),
    )
    return info, asset_map


def _read_parquet_url(url: str, *, token: str | None = None) -> pd.DataFrame:
    headers = {"User-Agent": "feeder-sync-oracle"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urlrequest.Request(url=url, headers=headers)
    try:
        with urlrequest.urlopen(req) as resp:
            data = resp.read()
    except urlerror.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Release asset download failed: {url} status={e.code} body={body[:300]}") from e

    return pd.read_parquet(BytesIO(data))


def _download_url_to_file(url: str, out_path: Path, *, token: str | None = None) -> None:
    headers = {"User-Agent": "feeder-sync-oracle"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urlrequest.Request(url=url, headers=headers)
    try:
        with urlrequest.urlopen(req) as resp, out_path.open("wb") as fp:
            while True:
                chunk = resp.read(8 * 1024 * 1024)
                if not chunk:
                    break
                fp.write(chunk)
    except urlerror.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Release asset download failed: {url} status={e.code} body={body[:300]}") from e


def _ensure_master_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    defaults: dict[str, Any] = {
        "Code": "",
        "Name": "",
        "Market": "KOSPI",
        "IndustryLarge": "",
        "IndustryMid": "",
        "IndustrySmall": "",
        "SharesOutstanding": pd.NA,
    }
    for col, default_val in defaults.items():
        if col not in out.columns:
            out[col] = default_val
    return out


def _feature_to_std_df(feature_df: pd.DataFrame) -> pd.DataFrame:
    out = feature_df.copy()

    required_cols = ["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required_cols if c not in out.columns]
    if missing:
        raise ValueError(f"Release feature parquet missing columns: {', '.join(missing)}")

    if "MarketCap" not in out.columns:
        out["MarketCap"] = pd.NA

    out, missing_rs = _attach_release_rs_columns(out)
    if missing_rs:
        logger.warning("Release parquet missing RS columns; fill NULL: %s", ", ".join(missing_rs))

    out = out[
        [
            "Date",
            "Ticker",
            "Open",
            "High",
            "Low",
            "Close",
            "Volume",
            "MarketCap",
            "RS_1M",
            "RS_3M",
            "RS_6M",
            "RS_12M",
            "RS_WEIGHTED",
        ]
    ].copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.normalize()
    out["Ticker"] = out["Ticker"].astype(str).str.zfill(6)
    for c in [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "MarketCap",
        "RS_1M",
        "RS_3M",
        "RS_6M",
        "RS_12M",
        "RS_WEIGHTED",
    ]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out["RS_WEIGHTED"] = _compute_weighted_rs(out)
    out = out.dropna(subset=["Date", "Ticker", "Close"]).reset_index(drop=True)
    return out


def _normalize_ticker(v: object) -> str:
    s = str(v).strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return s.zfill(6)


def load_release_collection(
    *,
    repo: str,
    tag: str | None,
    token: str | None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> ReleaseCollection:
    release, assets = _resolve_release(repo, tag=tag, token=token)

    feature_asset = assets.get("korea_universe_feature_frame.parquet")
    master_asset = assets.get("krx_stock_master.parquet")
    if not feature_asset or not master_asset:
        available = ", ".join(sorted(assets.keys()))
        raise RuntimeError(
            "Required release assets not found: "
            "korea_universe_feature_frame.parquet, krx_stock_master.parquet "
            f"(available={available})"
        )

    feature_df = _read_parquet_url(feature_asset, token=token)
    master_raw = _ensure_master_columns(_read_parquet_url(master_asset, token=token))

    std_df = _feature_to_std_df(feature_df)
    if start_date:
        s = pd.to_datetime(start_date, errors="coerce")
        std_df = std_df[std_df["Date"] >= s]
    if end_date:
        e = pd.to_datetime(end_date, errors="coerce")
        std_df = std_df[std_df["Date"] <= e]

    industry_df = _build_industry_df(master_raw)
    master_df = _build_master_df(master_raw)
    price_df, quality_metrics = _build_price_df(std_df, master_raw=master_raw)
    dividend_df = pd.DataFrame(
        columns=[
            "TICKER",
            "EX_DIVIDEND_DATE",
            "DIVIDEND_PER_SHARE",
            "RECORD_DATE",
            "PAYMENT_DATE",
            "DIVIDEND_TYPE",
        ]
    )
    quality_metrics["dividend_row_count"] = 0

    result = CollectionResult(
        industry_df=industry_df,
        master_df=master_df,
        price_df=price_df,
        dividend_df=dividend_df,
        quality_metrics=quality_metrics,
    )

    return ReleaseCollection(result=result, release=release)


def prepare_release_data(*, repo: str, tag: str | None, token: str | None) -> ReleasePreparedData:
    release, assets = _resolve_release(repo, tag=tag, token=token)

    feature_asset = assets.get("korea_universe_feature_frame.parquet")
    master_asset = assets.get("krx_stock_master.parquet")
    if not feature_asset or not master_asset:
        available = ", ".join(sorted(assets.keys()))
        raise RuntimeError(
            "Required release assets not found: "
            "korea_universe_feature_frame.parquet, krx_stock_master.parquet "
            f"(available={available})"
        )

    temp_dir = tempfile.mkdtemp(prefix="release_ingest_")
    temp_path = Path(temp_dir)
    feature_path = temp_path / "korea_universe_feature_frame.parquet"
    master_path = temp_path / "krx_stock_master.parquet"

    _download_url_to_file(feature_asset, feature_path, token=token)
    _download_url_to_file(master_asset, master_path, token=token)

    master_raw = _ensure_master_columns(pd.read_parquet(master_path))
    industry_df = _build_industry_df(master_raw)
    master_df = _build_master_df(master_raw)

    shares_map = (
        master_raw.assign(Code=master_raw["Code"].apply(_normalize_ticker))
        .drop_duplicates(subset=["Code"])
        .set_index("Code")["SharesOutstanding"]
    )
    shares_map = pd.to_numeric(shares_map, errors="coerce")
    allowed_tickers = set(master_df["TICKER"].astype(str).tolist())

    return ReleasePreparedData(
        release=release,
        temp_dir=temp_dir,
        master_parquet_path=str(master_path),
        feature_parquet_path=str(feature_path),
        industry_df=industry_df,
        master_df=master_df,
        shares_map=shares_map,
        allowed_tickers=allowed_tickers,
    )


def iter_release_price_batches(
    prepared: ReleasePreparedData,
    *,
    start_date: str | None,
    end_date: str | None,
    batch_rows: int = 200000,
):
    parquet = pq.ParquetFile(prepared.feature_parquet_path)
    schema_cols = set(parquet.schema.names)

    required = ["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in schema_cols]
    if missing:
        raise ValueError(f"Release feature parquet missing columns: {', '.join(missing)}")

    read_cols = ["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]
    if "MarketCap" in schema_cols:
        read_cols.append("MarketCap")

    rs_source_cols: dict[str, str | None] = {}
    missing_rs_cols: list[str] = []
    for target, aliases in RS_COLUMN_ALIASES.items():
        src = _resolve_col_case_insensitive(schema_cols, aliases)
        rs_source_cols[target] = src
        if src is None:
            missing_rs_cols.append(target)
        elif src not in read_cols:
            read_cols.append(src)

    if missing_rs_cols:
        logger.warning("Release parquet missing RS columns; fill NULL: %s", ", ".join(missing_rs_cols))

    start_ts = pd.to_datetime(start_date, errors="coerce") if start_date else None
    end_ts = pd.to_datetime(end_date, errors="coerce") if end_date else None

    for rb in parquet.iter_batches(batch_size=max(1, int(batch_rows)), columns=read_cols):
        df = rb.to_pandas()
        if "MarketCap" not in df.columns:
            df["MarketCap"] = pd.NA

        for target, source in rs_source_cols.items():
            if source is None or source not in df.columns:
                df[target] = pd.NA
            else:
                df[target] = pd.to_numeric(df[source], errors="coerce")

        df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
        if start_ts is not None:
            df = df[df["Date"] >= start_ts]
        if end_ts is not None:
            df = df[df["Date"] <= end_ts]
        if df.empty:
            continue

        df["Ticker"] = df["Ticker"].apply(_normalize_ticker)
        for c in [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume",
            "MarketCap",
            "RS_1M",
            "RS_3M",
            "RS_6M",
            "RS_12M",
            "RS_WEIGHTED",
        ]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["RS_WEIGHTED"] = _compute_weighted_rs(df)

        df = df.dropna(subset=["Date", "Ticker", "Close"]).reset_index(drop=True)
        if df.empty:
            continue

        before_ticker_filter = len(df)
        df = df[df["Ticker"].isin(prepared.allowed_tickers)].reset_index(drop=True)
        dropped_unknown_tickers = int(before_ticker_filter - len(df))
        if df.empty:
            continue

        missing_before = int(df["MarketCap"].isna().sum())
        computed = df["Close"] * pd.to_numeric(df["Ticker"].map(prepared.shares_map), errors="coerce")
        df["MarketCap"] = df["MarketCap"].combine_first(computed)
        missing_after = int(df["MarketCap"].isna().sum())
        df["MarketCap"] = df["MarketCap"].fillna(0)
        zero_final = int((df["MarketCap"] == 0).sum())

        out = df.rename(
            columns={
                "Date": "PRICE_DATE",
                "Ticker": "TICKER",
                "Open": "OPEN_PRICE",
                "High": "HIGH_PRICE",
                "Low": "LOW_PRICE",
                "Close": "CLOSE_PRICE",
                "Volume": "VOLUME",
                "MarketCap": "MARKET_CAP",
            }
        ).copy()
        out["ADJ_CLOSE"] = out["CLOSE_PRICE"]
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
                "RS_1M",
                "RS_3M",
                "RS_6M",
                "RS_12M",
                "RS_WEIGHTED",
            ]
        ]

        metrics = {
            "market_cap_missing_before": missing_before,
            "market_cap_missing_after_enrichment": missing_after,
            "market_cap_zero_final": zero_final,
            "price_row_count": int(len(out)),
            "dropped_unknown_ticker_rows": dropped_unknown_tickers,
        }
        yield out, metrics


def estimate_release_batch_count(prepared: ReleasePreparedData, *, batch_rows: int = 200000) -> int:
    parquet = pq.ParquetFile(prepared.feature_parquet_path)
    total_rows = int(parquet.metadata.num_rows) if parquet.metadata is not None else 0
    if total_rows <= 0:
        return 0
    size = max(1, int(batch_rows))
    return (total_rows + size - 1) // size