from __future__ import annotations

import pandas as pd

from .indicators import MANSFIELD_RS_SMA_WINDOW


INDUSTRY_LEVEL_L = "L"
INDUSTRY_LEVEL_LM = "LM"
INDUSTRY_LEVEL_LMS = "LMS"
INDUSTRY_LEVELS = [INDUSTRY_LEVEL_L, INDUSTRY_LEVEL_LM, INDUSTRY_LEVEL_LMS]


def _normalize_industry_value(v: object) -> str:
    s = "" if v is None else str(v).strip()
    if s.lower() in {"nan", "none"}:
        return ""
    return s


def _industry_key_large(large: str) -> str:
    return large or "Unknown"


def _industry_key_large_mid(large: str, mid: str) -> str:
    return f"{large or 'Unknown'}||{mid or 'Unknown'}"


def _industry_key_large_mid_small(large: str, mid: str, small: str) -> str:
    return f"{large or 'Unknown'}||{mid or 'Unknown'}||{small or 'Unknown'}"


def compute_universe_equal_weight_benchmark_close_by_date(
    feature_df: pd.DataFrame,
    *,
    global_dates: pd.DatetimeIndex,
) -> pd.Series:
    """
    Build an equal-weight universe benchmark index (base 100).
    """
    df = feature_df[["Date", "Ticker", "Close"]].copy()
    df["Ticker"] = df["Ticker"].astype(str).str.strip().str.zfill(6)
    df["Date"] = pd.to_datetime(df["Date"], errors="raise").dt.normalize()
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Date", "Ticker", "Close"]).sort_values(["Ticker", "Date"])

    df["Ret"] = df.groupby("Ticker", sort=False)["Close"].pct_change()
    u = df.groupby("Date", sort=True).agg(UniverseReturn=("Ret", "mean")).reset_index()

    u = u.set_index("Date").reindex(global_dates).sort_index()
    u["UniverseReturn"] = pd.to_numeric(u["UniverseReturn"], errors="coerce").fillna(0.0)
    bench = (1.0 + u["UniverseReturn"]).cumprod() * 100.0
    bench.name = "UniverseClose"
    bench.index = pd.DatetimeIndex(pd.to_datetime(bench.index, errors="raise")).normalize()
    return bench


def compute_industry_feature_frame(
    feature_df: pd.DataFrame,
    *,
    master_df: pd.DataFrame,
    benchmark_close_by_date: pd.Series | None,
    level: str,
    global_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """
    Build industry strength frame (equal-weight industry index + Mansfield RS).
    Output is long format:
      Date, Level, IndustryLarge, IndustryMid, IndustrySmall, IndustryKey,
      IndustryClose, IndustryReturn, ConstituentCount, MansfieldRS
    """
    if feature_df is None or feature_df.empty:
        raise ValueError("feature_df is empty")
    if master_df is None or master_df.empty:
        raise ValueError("master_df is empty")
    if level not in INDUSTRY_LEVELS:
        raise ValueError(f"invalid industry level: {level}")

    df = feature_df[["Date", "Ticker", "Close"]].copy()
    df["Ticker"] = df["Ticker"].astype(str).str.strip().str.zfill(6)
    df["Date"] = pd.to_datetime(df["Date"], errors="raise").dt.normalize()
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Date", "Ticker", "Close"]).sort_values(["Ticker", "Date"])

    m = master_df.copy()
    if "Code" not in m.columns:
        raise ValueError("master_df missing Code")
    m["Code"] = m["Code"].astype(str).str.strip().str.zfill(6)
    
    # Exclude ETF items from industry strength calculation
    if "Market" in m.columns:
        m = m[m["Market"] != "ETF"]
    
    for c in ["IndustryLarge", "IndustryMid", "IndustrySmall"]:
        if c not in m.columns:
            m[c] = ""
        m[c] = m[c].apply(_normalize_industry_value)

    df = df.merge(
        m[["Code", "IndustryLarge", "IndustryMid", "IndustrySmall"]],
        left_on="Ticker",
        right_on="Code",
        how="left",
    ).drop(columns=["Code"])

    df["IndustryLarge"] = df["IndustryLarge"].apply(_normalize_industry_value)
    df["IndustryMid"] = df["IndustryMid"].apply(_normalize_industry_value)
    df["IndustrySmall"] = df["IndustrySmall"].apply(_normalize_industry_value)

    if level == INDUSTRY_LEVEL_L:
        df["IndustryKey"] = df["IndustryLarge"].map(_industry_key_large)
        out_large = df["IndustryLarge"].map(lambda x: x or "Unknown")
        out_mid = pd.Series([""] * len(df), index=df.index)
        out_small = pd.Series([""] * len(df), index=df.index)
    elif level == INDUSTRY_LEVEL_LM:
        df["IndustryKey"] = [_industry_key_large_mid(a, b) for a, b in zip(df["IndustryLarge"], df["IndustryMid"])]
        out_large = df["IndustryLarge"].map(lambda x: x or "Unknown")
        out_mid = df["IndustryMid"].map(lambda x: x or "Unknown")
        out_small = pd.Series([""] * len(df), index=df.index)
    else:
        df["IndustryKey"] = [
            _industry_key_large_mid_small(a, b, c) for a, b, c in zip(df["IndustryLarge"], df["IndustryMid"], df["IndustrySmall"])
        ]
        out_large = df["IndustryLarge"].map(lambda x: x or "Unknown")
        out_mid = df["IndustryMid"].map(lambda x: x or "Unknown")
        out_small = df["IndustrySmall"].map(lambda x: x or "Unknown")

    df["IndustryLargeOut"] = out_large
    df["IndustryMidOut"] = out_mid
    df["IndustrySmallOut"] = out_small

    df["Ret"] = df.groupby("Ticker", sort=False)["Close"].pct_change()

    g = (
        df.groupby(["IndustryKey", "Date"], sort=True)
        .agg(IndustryReturn=("Ret", "mean"), ConstituentCount=("Ret", "count"))
        .reset_index()
    )

    keys = sorted(g["IndustryKey"].unique().tolist())
    if not keys:
        raise ValueError("no industry keys")

    full_idx = pd.MultiIndex.from_product([keys, global_dates], names=["IndustryKey", "Date"])
    g = g.set_index(["IndustryKey", "Date"]).reindex(full_idx).sort_index()
    # Optimize to float32 for storage efficiency
    g["IndustryReturn"] = pd.to_numeric(g["IndustryReturn"], errors="coerce").fillna(0.0).astype("float32")
    # Optimize to int16 (max constituents ~1200 fits safely in int16 max 32767)
    g["ConstituentCount"] = pd.to_numeric(g["ConstituentCount"], errors="coerce").fillna(0).astype("int16")

    # Optimize to float32 for storage efficiency
    g["IndustryClose"] = ((1.0 + g["IndustryReturn"]).groupby(level=0, sort=False).cumprod() * 100.0).astype("float32")

    out = g.reset_index()
    out["Level"] = level

    key_map = (
        df[["IndustryKey", "IndustryLargeOut", "IndustryMidOut", "IndustrySmallOut"]]
        .drop_duplicates(subset=["IndustryKey"])
        .set_index("IndustryKey")
    )
    out["IndustryLarge"] = out["IndustryKey"].map(key_map["IndustryLargeOut"]).fillna("Unknown")
    out["IndustryMid"] = out["IndustryKey"].map(key_map["IndustryMidOut"]).fillna("")
    out["IndustrySmall"] = out["IndustryKey"].map(key_map["IndustrySmallOut"]).fillna("")

    if benchmark_close_by_date is not None and not benchmark_close_by_date.empty:
        b = out["Date"].map(benchmark_close_by_date)
        b = pd.to_numeric(b, errors="coerce")
        rs_raw = out["IndustryClose"] / b
        rs_sma = rs_raw.groupby(out["IndustryKey"]).transform(
            lambda s: s.rolling(window=MANSFIELD_RS_SMA_WINDOW, min_periods=MANSFIELD_RS_SMA_WINDOW).mean()
        )
        # Optimize to float32 for storage efficiency
        out["MansfieldRS"] = ((rs_raw / rs_sma - 1.0) * 100.0).astype("float32")
    else:
        out["MansfieldRS"] = pd.NA

    return out[
        [
            "Date",
            "Level",
            "IndustryLarge",
            "IndustryMid",
            "IndustrySmall",
            "IndustryKey",
            "IndustryClose",
            "IndustryReturn",
            "ConstituentCount",
            "MansfieldRS",
        ]
    ].sort_values(["Level", "IndustryLarge", "IndustryMid", "IndustrySmall", "Date"])

