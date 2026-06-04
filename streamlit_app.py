import streamlit as st
import requests
import pandas as pd
import io
import os
import json
import altair as alt
import datetime as dt
import duckdb
from collections.abc import Iterable

st.set_page_config(
    page_title="Korea Stock Feature Cache Inspector",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("📊 Korea Stock Feature Cache Inspector")

# Settings UI removed (use defaults)
default_repo = "capybara-dance/capybara_fetcher"
repo_name = default_repo
github_token = ""

@st.cache_data(ttl=60)
def get_releases(repo, token=None):
    if not repo:
        return []
        
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    
    url = f"https://api.github.com/repos/{repo}/releases"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            st.error(f"Repository not found: {repo}")
            return []
        else:
            st.error(f"Failed to fetch releases: {response.status_code} {response.reason}")
            return []
    except Exception as e:
        st.error(f"Connection error: {e}")
        return []

@st.cache_data(ttl=300)
def load_parquet_from_url(url, token=None):
    headers = {}
    # Private asset 다운로드 시에는 token 헤더와 Accept 헤더가 필요할 수 있음
    # 하지만 browser_download_url은 보통 Public이면 바로 접근 가능하고,
    # Private이면 API url을 써야 하는데 여기서는 browser_download_url을 사용함.
    # 만약 Private Repo라면 token이 있어도 browser_download_url로 직접 requests.get 하면 404가 뜰 수 있음.
    # (API url: https://api.github.com/repos/:owner/:repo/releases/assets/:asset_id)
    # 복잡성을 피하기 위해 Public Repo 가정이거나, Token이 있으면 시도해봄.
    
    if token:
        headers["Authorization"] = f"token {token}"
    
    try:
        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()
        return pd.read_parquet(io.BytesIO(response.content))
    except Exception as e:
        st.error(f"Error loading parquet: {e}")
        return None

@st.cache_resource
def get_duckdb_conn():
    con = duckdb.connect(database=":memory:")
    # HTTP range reads for large parquet
    con.execute("INSTALL httpfs;")
    con.execute("LOAD httpfs;")
    return con

@st.cache_data(ttl=300)
def query_feature_parquet(
    parquet_url: str,
    ticker: str,
    start_date: dt.date,
    end_date: dt.date,
    columns: tuple[str, ...],
) -> pd.DataFrame:
    con = get_duckdb_conn()
    cols_sql = ", ".join([f'"{c}"' for c in columns])
    sql = f"""
        SELECT {cols_sql}
        FROM read_parquet(?)
        WHERE "Ticker" = ?
          AND "Date" >= ?
          AND "Date" <= ?
        ORDER BY "Date"
    """
    return con.execute(sql, [parquet_url, ticker, str(start_date), str(end_date)]).df()

@st.cache_data(ttl=300)
def query_feature_date_bounds(parquet_url: str, ticker: str):
    con = get_duckdb_conn()
    sql = """
        SELECT min("Date") AS min_date, max("Date") AS max_date
        FROM read_parquet(?)
        WHERE "Ticker" = ?
    """
    row = con.execute(sql, [parquet_url, ticker]).fetchone()
    return row[0], row[1]

@st.cache_data(ttl=300)
def get_parquet_columns(parquet_url: str) -> list[str]:
    """
    원격 parquet의 컬럼 목록을 가볍게 조회합니다. (row 스캔 없이 LIMIT 0)
    """
    con = get_duckdb_conn()
    df0 = con.execute("SELECT * FROM read_parquet(?) LIMIT 0", [parquet_url]).df()
    return list(df0.columns)

@st.cache_data(ttl=300)
def query_industry_parquet(
    parquet_url: str,
    level: str,
    industry_large: str,
    industry_mid: str,
    industry_small: str,
    start_date: dt.date,
    end_date: dt.date,
    columns: tuple[str, ...],
) -> pd.DataFrame:
    con = get_duckdb_conn()
    cols_sql = ", ".join([f'"{c}"' for c in columns])
    sql = f"""
        SELECT {cols_sql}
        FROM read_parquet(?)
        WHERE "Level" = ?
          AND "IndustryLarge" = ?
          AND "IndustryMid" = ?
          AND "IndustrySmall" = ?
          AND "Date" >= ?
          AND "Date" <= ?
        ORDER BY "Date"
    """
    return con.execute(
        sql,
        [
            parquet_url,
            level,
            industry_large,
            industry_mid,
            industry_small,
            str(start_date),
            str(end_date),
        ],
    ).df()

@st.cache_data(ttl=300)
def query_industry_date_bounds(
    parquet_url: str,
    level: str,
    industry_large: str,
    industry_mid: str,
    industry_small: str,
):
    con = get_duckdb_conn()
    sql = """
        SELECT min("Date") AS min_date, max("Date") AS max_date
        FROM read_parquet(?)
        WHERE "Level" = ?
          AND "IndustryLarge" = ?
          AND "IndustryMid" = ?
          AND "IndustrySmall" = ?
    """
    row = con.execute(sql, [parquet_url, level, industry_large, industry_mid, industry_small]).fetchone()
    return row[0], row[1]

@st.cache_data(ttl=300)
def query_industry_level_date_bounds(parquet_url: str, level: str):
    con = get_duckdb_conn()
    sql = """
        SELECT min("Date") AS min_date, max("Date") AS max_date
        FROM read_parquet(?)
        WHERE "Level" = ?
    """
    row = con.execute(sql, [parquet_url, level]).fetchone()
    return row[0], row[1]

@st.cache_data(ttl=300)
def query_industry_top_by_rs(parquet_url: str, level: str, asof_date: dt.date, limit: int = 5) -> pd.DataFrame:
    """
    지정 날짜(asof) 기준(해당 날짜 이전 최신 거래일) MansfieldRS 상위 업종을 조회합니다.
    """
    con = get_duckdb_conn()
    sql = """
        SELECT
          "IndustryLarge",
          "IndustryMid",
          "IndustrySmall",
          "MansfieldRS",
          "ConstituentCount",
          "Date"
        FROM read_parquet(?)
        WHERE "Level" = ?
          AND "Date" = (
            SELECT max("Date")
            FROM read_parquet(?)
            WHERE "Level" = ?
              AND "Date" <= ?
          )
          AND "MansfieldRS" IS NOT NULL
        ORDER BY "MansfieldRS" DESC NULLS LAST
        LIMIT ?
    """
    return con.execute(sql, [parquet_url, level, parquet_url, level, str(asof_date), int(limit)]).df()

@st.cache_data(ttl=300)
def query_industry_rank_by_rs(parquet_url: str, level: str, asof_date: dt.date) -> pd.DataFrame:
    """
    지정 날짜(asof) 기준(해당 날짜 이전 최신 거래일) 업종별 MansfieldRS 랭킹(내림차순)을 반환합니다.
    """
    con = get_duckdb_conn()
    sql = """
        SELECT
          "IndustryLarge",
          "IndustryMid",
          "IndustrySmall",
          "MansfieldRS",
          "ConstituentCount",
          "Date"
        FROM read_parquet(?)
        WHERE "Level" = ?
          AND "Date" = (
            SELECT max("Date")
            FROM read_parquet(?)
            WHERE "Level" = ?
              AND "Date" <= ?
          )
        ORDER BY "MansfieldRS" DESC NULLS LAST
    """
    return con.execute(sql, [parquet_url, level, parquet_url, level, str(asof_date)]).df()

def _normalize_na_to_empty(v: object) -> str:
    """Normalize NA/None/nan values to empty string for display."""
    if v is None or pd.isna(v):
        return ""
    s = str(v).strip()
    # Handle string representations of NA
    if s.lower() in {"nan", "none", "<na>", "na"}:
        return ""
    return s

def _industry_label(level: str, large: str, mid: str, small: str) -> str:
    # Normalize NA values before creating label
    large = _normalize_na_to_empty(large) or "Unknown"
    mid = _normalize_na_to_empty(mid) or "Unknown"
    small = _normalize_na_to_empty(small) or "Unknown"
    
    if level == "L":
        return f"{large}"
    if level == "LM":
        return f"{large} / {mid}"
    return f"{large} / {mid} / {small}"

@st.cache_data(ttl=300)
def query_tickers_rs_by_ticker_list(
    feature_url: str,
    tickers: list[str],
    asof_date: dt.date,
    limit: int = 10,
) -> pd.DataFrame:
    """
    지정 종목 리스트의 RS 값을 조회합니다.
    asof_date 기준(해당 날짜 이전 최신 거래일) MansfieldRS 상위 종목을 반환합니다.
    """
    if not tickers:
        return pd.DataFrame(columns=["Ticker", "MansfieldRS", "Date"])
    
    con = get_duckdb_conn()
    # IN 절을 위한 ticker 리스트 준비
    ticker_placeholders = ",".join(["?" for _ in tickers])
    sql = f"""
        SELECT
          "Ticker",
          "MansfieldRS",
          "Date"
        FROM read_parquet(?)
        WHERE "Ticker" IN ({ticker_placeholders})
          AND "Date" = (
            SELECT max("Date")
            FROM read_parquet(?)
            WHERE "Ticker" IN ({ticker_placeholders})
              AND "Date" <= ?
          )
          AND "MansfieldRS" IS NOT NULL
        ORDER BY "MansfieldRS" DESC NULLS LAST
        LIMIT ?
    """
    params = [feature_url] + tickers + [feature_url] + tickers + [str(asof_date), int(limit)]
    return con.execute(sql, params).df()

@st.cache_data(ttl=300)
def load_json_from_url(url, token=None):
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()
        return json.loads(response.content.decode("utf-8"))
    except Exception as e:
        st.error(f"Error loading metadata json: {e}")
        return None

def _ensure_datetime(series: pd.Series) -> pd.Series:
    # Robust conversion for parquet-loaded types (datetime64, date, int timestamp, etc.)
    return pd.to_datetime(series, errors="coerce")

def _pick_default_date_window(dmin: pd.Timestamp, dmax: pd.Timestamp, days: int = 365) -> tuple[pd.Timestamp, pd.Timestamp]:
    if pd.isna(dmin) or pd.isna(dmax):
        return dmin, dmax
    start = max(dmin, dmax - pd.Timedelta(days=days))
    return start, dmax

def _axis_assignment(df: pd.DataFrame, base_col: str, other_cols: list[str]) -> tuple[list[str], list[str]]:
    """
    Heuristic: columns with range far from base go to right axis.
    """
    if base_col not in df.columns:
        return [base_col], other_cols
    base = pd.to_numeric(df[base_col], errors="coerce")
    base_range = float((base.max() - base.min()) if base.notna().any() else 0.0)
    if base_range <= 0:
        return [base_col] + other_cols, []

    left_cols = [base_col]
    right_cols: list[str] = []
    for c in other_cols:
        s = pd.to_numeric(df[c], errors="coerce")
        r = float((s.max() - s.min()) if s.notna().any() else 0.0)
        if r <= 0:
            left_cols.append(c)
            continue
        ratio = r / base_range
        if ratio >= 10 or ratio <= 0.1:
            right_cols.append(c)
        else:
            left_cols.append(c)
    return left_cols, right_cols

def _build_newhigh_marker_layer(
    df: pd.DataFrame,
    date_col: str,
    y_col: str,
    *,
    title: str = "New High (1Y)",
    color: str = "#f59e0b",
    size: int = 90,
):
    if "IsNewHigh1Y" not in df.columns:
        return None
    if date_col not in df.columns:
        return None
    if y_col not in df.columns:
        return None

    m = df[df["IsNewHigh1Y"] == True].copy()  # noqa: E712 (pandas nullable boolean)
    if m.empty:
        return None

    m = m[[date_col, y_col]].copy().sort_values(date_col)
    m["Event"] = title
    return (
        alt.Chart(m)
        .mark_point(shape="triangle-up", filled=True, size=size, color=color)
        .encode(
            x=alt.X(f"{date_col}:T", title="Date"),
            # Disable axis on marker layer so it doesn't override main axis
            y=alt.Y(f"{y_col}:Q", axis=None),
            tooltip=[
                alt.Tooltip(f"{date_col}:T"),
                alt.Tooltip(f"{y_col}:Q", title=y_col),
                alt.Tooltip("Event:N"),
            ],
        )
    )

def _build_dual_axis_chart(
    df: pd.DataFrame,
    date_col: str,
    left_cols: list[str],
    right_cols: list[str],
    *,
    marker_layer=None,
):
    base = df[[date_col] + sorted(set(left_cols + right_cols))].copy()
    base = base.sort_values(date_col)

    def melt(cols: list[str]) -> pd.DataFrame:
        if not cols:
            return pd.DataFrame(columns=[date_col, "metric", "value"])
        return base[[date_col] + cols].melt(id_vars=[date_col], var_name="metric", value_name="value")

    left_long = melt(left_cols)
    right_long = melt(right_cols)

    x = alt.X(f"{date_col}:T", title="Date")
    left = (
        alt.Chart(left_long)
        .mark_line()
        .encode(
            x=x,
            y=alt.Y("value:Q", title="Left axis"),
            color=alt.Color("metric:N", title="Metric"),
            tooltip=[alt.Tooltip(f"{date_col}:T"), alt.Tooltip("metric:N"), alt.Tooltip("value:Q")],
        )
    )
    if marker_layer is not None:
        # Marker should share the left (price-scale) axis
        # NOTE: keep the main line chart as the last layer.
        # Some Vega-Lite/Altair versions can suppress the shared axis
        # if the last layer sets axis=None (our marker layer does).
        left = alt.layer(marker_layer, left)

    if right_cols:
        right = (
            alt.Chart(right_long)
            .mark_line(strokeDash=[6, 2])
            .encode(
                x=x,
                y=alt.Y("value:Q", axis=alt.Axis(orient="right", title="Right axis")),
                color=alt.Color("metric:N", legend=None),
                tooltip=[alt.Tooltip(f"{date_col}:T"), alt.Tooltip("metric:N"), alt.Tooltip("value:Q")],
            )
        )
        return alt.layer(left, right).resolve_scale(y="independent")

    return left

def _build_candlestick_chart(df: pd.DataFrame, date_col: str = "Date", marker_layer=None):
    """
    Candlestick chart from OHLC columns.
    - Rule: Low..High
    - Bar: Open..Close (green up, red down)
    """
    needed = {"Open", "High", "Low", "Close", date_col}
    if not needed.issubset(set(df.columns)):
        return None

    base = df[[date_col, "Open", "High", "Low", "Close"]].copy()
    base = base.sort_values(date_col)
    base["is_up"] = (pd.to_numeric(base["Close"], errors="coerce") >= pd.to_numeric(base["Open"], errors="coerce"))

    x = alt.X(f"{date_col}:T", title="Date")

    wick = (
        alt.Chart(base)
        .mark_rule()
        .encode(
            x=x,
            y=alt.Y("Low:Q", title="Price"),
            y2="High:Q",
            color=alt.condition("datum.is_up", alt.value("#16a34a"), alt.value("#dc2626")),
            tooltip=[
                alt.Tooltip(f"{date_col}:T"),
                alt.Tooltip("Open:Q"),
                alt.Tooltip("High:Q"),
                alt.Tooltip("Low:Q"),
                alt.Tooltip("Close:Q"),
            ],
        )
    )

    body = (
        alt.Chart(base)
        .mark_bar()
        .encode(
            x=x,
            y=alt.Y("Open:Q", title=None),
            y2="Close:Q",
            color=alt.condition("datum.is_up", alt.value("#16a34a"), alt.value("#dc2626")),
            tooltip=[
                alt.Tooltip(f"{date_col}:T"),
                alt.Tooltip("Open:Q"),
                alt.Tooltip("High:Q"),
                alt.Tooltip("Low:Q"),
                alt.Tooltip("Close:Q"),
            ],
        )
    )

    # Put marker first so axis/title from candle layers remain visible.
    layers = [marker_layer, wick, body] if marker_layer is not None else [wick, body]
    return alt.layer(*layers)

def _build_metric_overlay_lines(df: pd.DataFrame, date_col: str, cols: list[str], axis_orient: str, show_legend: bool):
    if not cols:
        return None
    base = df[[date_col] + cols].copy().sort_values(date_col)
    long = base.melt(id_vars=[date_col], var_name="metric", value_name="value")
    axis = alt.Axis(orient=axis_orient, title=("Right axis" if axis_orient == "right" else "Left axis"))
    return (
        alt.Chart(long)
        .mark_line()
        .encode(
            x=alt.X(f"{date_col}:T", title="Date"),
            y=alt.Y("value:Q", axis=axis),
            color=alt.Color("metric:N", title="Metric", legend=None if not show_legend else alt.Legend()),
            tooltip=[alt.Tooltip(f"{date_col}:T"), alt.Tooltip("metric:N"), alt.Tooltip("value:Q")],
        )
    )

def _build_candlestick_with_metrics(df: pd.DataFrame, date_col: str, metrics: list[str], *, marker_layer=None):
    candle = _build_candlestick_chart(df, date_col, marker_layer=marker_layer)
    if candle is None:
        return None

    metrics = [m for m in metrics if m in df.columns]
    if not metrics:
        return candle

    # Decide left/right for overlays based on Close scale
    # Even though we don't overlay Close as a line, use Close as baseline for scale heuristics.
    left_cols, right_cols = _axis_assignment(df, "Close", metrics)

    # Build left overlay list (optionally includes Close)
    left_overlay = [c for c in left_cols if c in metrics]
    right_overlay = [c for c in right_cols if c in metrics]

    left_lines = _build_metric_overlay_lines(
        df,
        date_col,
        left_overlay,
        axis_orient="left",
        show_legend=True,
    )
    right_lines = _build_metric_overlay_lines(
        df,
        date_col,
        right_overlay,
        axis_orient="right",
        show_legend=(left_lines is None),
    )
    if right_lines is not None:
        # Make right axis dashed for distinction
        right_lines = right_lines.mark_line(strokeDash=[6, 2])

    left_chart = candle if left_lines is None else alt.layer(candle, left_lines)
    if right_lines is None:
        return left_chart

    return alt.layer(left_chart, right_lines).resolve_scale(y="independent")

def find_meta_asset(assets, parquet_asset_name: str):
    """
    parquet 자산과 짝이 되는 meta json을 찾습니다.
    기본 규칙: <name>.parquet -> <name>.meta.json
    """
    expected = parquet_asset_name.replace(".parquet", ".meta.json")
    for a in assets:
        if a.get("name") == expected:
            return a
    return None

def find_asset_by_name(assets, asset_name: str):
    for a in assets:
        if a.get("name") == asset_name:
            return a
    return None

def pick_meta_asset(assets):
    meta_assets = [a for a in assets if a.get("name", "").endswith(".meta.json")]
    if not meta_assets:
        return None
    # Prefer the known default name if present
    for a in meta_assets:
        if a.get("name") == "korea_universe_feature_frame.meta.json":
            return a
    # Otherwise prefer assets that look like they belong to the feature frame
    for a in meta_assets:
        n = a.get("name", "").lower()
        if "feature" in n and "frame" in n:
            return a
    return meta_assets[0]

def pick_feature_asset(assets):
    parquet_assets = [a for a in assets if a.get("name", "").endswith(".parquet")]
    feature_assets = [
        a
        for a in parquet_assets
        if a.get("name") not in {"krx_stock_master.parquet", "korea_industry_feature_frame.parquet"}
    ]
    if not feature_assets:
        return None
    # Prefer the known default name if present
    for a in feature_assets:
        if a.get("name") == "korea_universe_feature_frame.parquet":
            return a
    # Otherwise prefer assets that look like they belong to the feature frame
    for a in feature_assets:
        n = a.get("name", "").lower()
        if "feature" in n and "frame" in n:
            return a
    return feature_assets[0]

def pick_industry_asset(assets):
    candidates = [a for a in assets if a.get("name", "").endswith(".parquet")]
    if not candidates:
        return None
    for a in candidates:
        if a.get("name") == "korea_industry_feature_frame.parquet":
            return a
    for a in candidates:
        if "industry" in (a.get("name", "").lower()):
            return a
    return None

def pick_krx_stock_master_asset(assets):
    candidates = [a for a in assets if a.get("name", "").endswith(".parquet")]
    if not candidates:
        return None
    for a in candidates:
        if a.get("name") == "krx_stock_master.parquet":
            return a
    for a in candidates:
        if "krx_stock_master" in (a.get("name", "").lower()):
            return a
    return None

def _collect_meta_messages(obj: object, *, max_items: int = 30) -> list[tuple[str, str]]:
    """
    meta.json 내부의 error/last_error/notes 같은 메시지를 path와 함께 수집합니다.
    너무 과도하게 표기되지 않도록 max_items로 제한합니다.
    Returns: list[(path, message)]
    """
    keys = {"error", "last_error", "notes"}
    out: list[tuple[str, str]] = []

    def is_meaningful(v: object) -> bool:
        if v is None:
            return False
        s = str(v).strip()
        return s not in {"", "-", "None", "null", "nan"}

    def walk(v: object, path: str) -> None:
        if len(out) >= max_items:
            return
        if isinstance(v, dict):
            for k, vv in v.items():
                p = f"{path}.{k}" if path else str(k)
                if str(k) in keys and is_meaningful(vv):
                    out.append((p, str(vv)))
                    if len(out) >= max_items:
                        return
                walk(vv, p)
        elif isinstance(v, list):
            for i, vv in enumerate(v):
                walk(vv, f"{path}[{i}]")

    walk(obj, "")
    return out

def _meta_health(meta: dict) -> tuple[list[str], list[str]]:
    """
    Returns: (errors, warnings) as human-readable strings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1) Universe fetch status
    uf = (meta or {}).get("universe_fetch") or {}
    if uf.get("success") is False:
        msg = uf.get("last_error") or uf.get("error") or "Universe fetch failed."
        errors.append(f"universe_fetch 실패: {msg}")

    # 2) Benchmark fetch for MansfieldRS
    mf = (((meta or {}).get("indicators") or {}).get("mansfield_rs") or {}).get("benchmark_fetch") or {}
    if mf and mf.get("success") is False:
        t = mf.get("ticker") or (mf.get("type") if isinstance(mf.get("type"), str) else None) or "benchmark"
        msg = mf.get("error") or "benchmark fetch failed."
        warnings.append(f"MansfieldRS 벤치마크({t}) fetch 실패: {msg}")

    # 3) Generic messages
    for p, m in _collect_meta_messages(meta):
        # skip duplicates from above (best-effort)
        if any(m in s for s in errors) or any(m in s for s in warnings):
            continue
        if p.endswith(".notes"):
            warnings.append(f"notes: {m}")
        elif ".error" in p or p.endswith(".last_error"):
            # treat as warning by default (could be non-fatal)
            warnings.append(f"{p}: {m}")

    return errors, warnings

# 메인 로직
if repo_name:
    releases = get_releases(repo_name, github_token)

    if releases:
        st.write(f"✅ Found {len(releases)} releases.")
        
        # 릴리스 선택
        release_options = {f"{r['name']} ({r['tag_name']})": r for r in releases}
        selected_option = st.selectbox("Select Release", list(release_options.keys()))
        
        if selected_option:
            selected_release = release_options[selected_option]
            
            with st.expander("Release Details", expanded=True):
                st.markdown(f"**Created at:** {selected_release['created_at']}")
                st.markdown(f"**Tag:** `{selected_release['tag_name']}`")
                st.markdown(selected_release['body'] if selected_release['body'] else "No description.")
            
            # Asset 찾기
            assets = selected_release.get('assets', [])

            st.subheader("📦 Assets")
            meta_asset = pick_meta_asset(assets)
            feature_asset = pick_feature_asset(assets)
            krx_master_asset = pick_krx_stock_master_asset(assets)
            industry_asset = pick_industry_asset(assets)

            # Keep loaded frames in session_state (so chart UI doesn't reset)
            if "krx_master_df" not in st.session_state:
                st.session_state["krx_master_df"] = None
            if "meta_obj" not in st.session_state:
                st.session_state["meta_obj"] = None

            # 1) 메타데이터: 릴리즈 선택 시 자동 로드/표시 (meta-only 릴리즈 지원)
            with st.expander("Metadata (meta.json)", expanded=False):
                if meta_asset:
                    st.write(f"**Meta asset:** `{meta_asset['name']}`")
                    meta = load_json_from_url(meta_asset["browser_download_url"], github_token)
                    if meta:
                        st.session_state["meta_obj"] = meta
                        # show meta health banner (also outside this expander via session_state)
                        errors, warnings = _meta_health(meta)
                        st.session_state["meta_health"] = {"errors": errors, "warnings": warnings}
                        col_a, col_b, col_c, col_d = st.columns(4)
                        col_a.metric("Start", meta.get("start_date", "-"))
                        col_b.metric("End", meta.get("end_date", "-"))
                        col_c.metric("Tickers", meta.get("ticker_count", 0))
                        col_d.metric("Rows", meta.get("rows", 0))
                        if errors:
                            st.error(" / ".join(errors))
                        if warnings:
                            # avoid massive warning block
                            st.warning("\n".join(warnings[:8]) + (f"\n… (+{len(warnings)-8} more)" if len(warnings) > 8 else ""))
                        st.json(meta)
                else:
                    st.info("No meta json found in this release.")

            # Meta health banner outside expander (so user notices)
            mh = st.session_state.get("meta_health") or {}
            mh_errors = mh.get("errors") or []
            mh_warnings = mh.get("warnings") or []
            if mh_errors:
                st.error(" / ".join(mh_errors))
            elif mh_warnings:
                st.warning("\n".join(mh_warnings[:6]) + (f"\n… (+{len(mh_warnings)-6} more)" if len(mh_warnings) > 6 else ""))

            # 1.5) KRX Stock Master: 버튼 클릭 시 로드
            with st.expander("KRX Stock Master (parquet)", expanded=False):
                if krx_master_asset:
                    st.write(f"**Master asset:** `{krx_master_asset['name']}`")
                    if st.button("Load KRX Stock Master", key="load_krx_master"):
                        with st.spinner("Downloading KRX stock master..."):
                            mdf = load_parquet_from_url(krx_master_asset["browser_download_url"], github_token)
                            if mdf is not None:
                                st.success("KRX stock master loaded successfully!")
                                st.session_state["krx_master_df"] = mdf
                    mdf_loaded = st.session_state.get("krx_master_df")
                    if mdf_loaded is not None:
                        st.write(f"**Loaded shape:** {mdf_loaded.shape}")
                        st.dataframe(mdf_loaded.head(500), use_container_width=True)
                else:
                    st.info("No `krx_stock_master.parquet` found in this release.")

            # 3) Feature data: 버튼 클릭 시 로드
            with st.expander("Feature Data (parquet)", expanded=True):
                if feature_asset:
                    st.write(f"**Feature asset:** `{feature_asset['name']}`")
                    st.info("Full download/load can crash for large universes. Charts below query by ticker/date without loading the whole file.")
                else:
                    st.info("No feature parquet found in this release.")

            # 3.5) Industry strength data
            with st.expander("Industry Strength Data (parquet)", expanded=False):
                if industry_asset:
                    st.write(f"**Industry asset:** `{industry_asset['name']}`")
                    industry_meta_asset = find_meta_asset(assets, industry_asset["name"])
                    if industry_meta_asset:
                        st.write(f"**Industry meta:** `{industry_meta_asset['name']}`")
                    st.info("Industry charts below query by industry/date without loading the whole file.")
                else:
                    st.info("No industry parquet found in this release.")

            # 4) Industry strength chart (A안 결과)
            # Place this section BEFORE the ticker chart, so it is visible
            # even when the ticker UI is long.
            if industry_asset is not None:
                st.subheader("🏭 Industry Strength (Mansfield RS)")
                industry_url = industry_asset["browser_download_url"]

                # Ensure KRX stock master is available (for industry list UI)
                master_df = st.session_state.get("krx_master_df")
                if (master_df is None or master_df.empty) and krx_master_asset is not None:
                    with st.spinner("Loading KRX stock master for industry lists..."):
                        mdf = load_parquet_from_url(krx_master_asset["browser_download_url"], github_token)
                        if mdf is not None and not mdf.empty:
                            st.session_state["krx_master_df"] = mdf
                            master_df = mdf

                level_label_to_value = {
                    "대분류 (L)": "L",
                    "대/중분류 (LM)": "LM",
                    "대/중/소분류 (LMS)": "LMS",
                }
                level_label = st.selectbox(
                    "Industry Level",
                    list(level_label_to_value.keys()),
                    index=1,  # default: "대/중분류 (LM)"
                    key="industry_level",
                )
                level = level_label_to_value[level_label]

                # Date range (industry-level bounds)
                try:
                    min_date, max_date = query_industry_level_date_bounds(industry_url, level)
                except Exception as e:
                    st.error(f"Failed to query industry date bounds (likely URL/access issue): {e}")
                    min_date, max_date = None, None

                if min_date is None or max_date is None:
                    st.warning("No industry data available.")
                else:
                    min_d = pd.to_datetime(min_date).date()
                    max_d = pd.to_datetime(max_date).date()
                    default_start = max(min_d, (pd.Timestamp(max_d) - pd.Timedelta(days=365)).date())
                    start_d, end_d = st.slider(
                        "Date range (Industry)",
                        min_value=min_d,
                        max_value=max_d,
                        value=(default_start, max_d),
                        key="industry_date_range",
                    )

                    # Top 5 (sorted by MansfieldRS) as-of end_d
                    top_df = query_industry_top_by_rs(industry_url, level, end_d, limit=5)
                    if top_df is None or top_df.empty:
                        st.info("Top 5 industries not available (MansfieldRS may be NA in this range).")
                        top_df = pd.DataFrame(columns=["IndustryLarge", "IndustryMid", "IndustrySmall", "MansfieldRS", "ConstituentCount", "Date"])

                    top_df = top_df.copy()
                    top_df["Label"] = top_df.apply(
                        lambda r: _industry_label(level, r["IndustryLarge"], r["IndustryMid"], r["IndustrySmall"]),
                        axis=1,
                    )

                    st.markdown("**Top 5 (as-of end date, sorted by MansfieldRS)**")

                    top5_display_df = top_df[["Date", "Label", "MansfieldRS", "ConstituentCount"]].copy()
                    top5_event = st.dataframe(
                        top5_display_df,
                        hide_index=True,
                        use_container_width=True,
                        on_select="rerun",
                        selection_mode="single-row",
                        key="top5_industry_df",
                    )
                    selected_industry_idx = (
                        int(top5_event.selection.rows[0]) if getattr(top5_event, "selection", None) and top5_event.selection.rows else None
                    )

                    include_top5 = st.checkbox("Include Top 5 in chart", value=True, key="industry_include_top5")

                    # Ranked list (sorted by MansfieldRS as-of end_d)
                    ranked_df = query_industry_rank_by_rs(industry_url, level, end_d)
                    if ranked_df is None or ranked_df.empty:
                        st.info("Industry ranking not available for this date.")
                        ranked_df = pd.DataFrame(
                            columns=["IndustryLarge", "IndustryMid", "IndustrySmall", "MansfieldRS", "ConstituentCount", "Date"]
                        )

                    ranked_df = ranked_df.copy()
                    ranked_df["Label"] = ranked_df.apply(
                        lambda r: _industry_label(level, r["IndustryLarge"], r["IndustryMid"], r["IndustrySmall"]),
                        axis=1,
                    )

                    # Build selection options ordered by RS (ranked_df order)
                    label_to_tuple: dict[str, tuple[str, str, str]] = {}
                    ranked_labels: list[str] = []
                    for _, r in ranked_df.iterrows():
                        lab = str(r["Label"])
                        tup = (str(r["IndustryLarge"]), str(r["IndustryMid"]), str(r["IndustrySmall"]))
                        if lab not in label_to_tuple:
                            label_to_tuple[lab] = tup
                            ranked_labels.append(lab)

                    top_labels = top_df["Label"].tolist() if include_top5 else []
                    top_label_set = set(top_labels)

                    search_q = st.text_input("Search industries to add", value="", key="industry_search")
                    extra_options = [l for l in ranked_labels if l not in top_label_set]
                    if search_q.strip():
                        s = search_q.strip().lower()
                        extra_options = [l for l in extra_options if s in l.lower()]

                    extra_labels = st.multiselect(
                        "추가 분류 선택 (선택한 업종도 차트에 표시)",
                        options=extra_options,
                        default=[],
                        key="industry_extra_labels",
                    )

                    labels_to_plot = top_labels + extra_labels
                    if not labels_to_plot:
                        st.info("No industries selected for chart.")
                    else:
                        # Query time series per selected industry and plot MansfieldRS only
                        series_frames: list[pd.DataFrame] = []
                        for lab in labels_to_plot:
                            tup = None
                            if lab in label_to_tuple:
                                tup = label_to_tuple[lab]
                            else:
                                # Fallback (top_df labels should still be resolvable here)
                                row = top_df[top_df["Label"] == lab]
                                if not row.empty:
                                    r0 = row.iloc[0]
                                    tup = (str(r0["IndustryLarge"]), str(r0["IndustryMid"]), str(r0["IndustrySmall"]))
                            if tup is None:
                                continue
                            a, b, c = tup
                            one = query_industry_parquet(
                                industry_url,
                                level,
                                a,
                                b,
                                c,
                                start_d,
                                end_d,
                                ("Date", "MansfieldRS", "ConstituentCount"),
                            )
                            if one is None or one.empty:
                                continue
                            one["Date"] = _ensure_datetime(one["Date"])
                            one["MansfieldRS"] = pd.to_numeric(one["MansfieldRS"], errors="coerce")
                            one["ConstituentCount"] = pd.to_numeric(one["ConstituentCount"], errors="coerce")
                            one = one.dropna(subset=["Date"]).sort_values("Date")
                            one["Industry"] = lab
                            series_frames.append(one[["Date", "Industry", "MansfieldRS", "ConstituentCount"]])

                        if not series_frames:
                            st.warning("No data to plot for selected industries.")
                        else:
                            plot_df = pd.concat(series_frames, ignore_index=True)
                            chart = (
                                alt.Chart(plot_df)
                                .mark_line()
                                .encode(
                                    x=alt.X("Date:T", title="Date"),
                                    y=alt.Y("MansfieldRS:Q", title="MansfieldRS"),
                                    color=alt.Color("Industry:N", title="Industry"),
                                    tooltip=[
                                        alt.Tooltip("Date:T"),
                                        alt.Tooltip("Industry:N"),
                                        alt.Tooltip("MansfieldRS:Q", format=".2f"),
                                        alt.Tooltip("ConstituentCount:Q", title="N"),
                                    ],
                                )
                            )
                            st.altair_chart(chart, use_container_width=True)

                    # 선택된 업종의 상위 RS 종목 표시
                    if feature_asset is not None and not top_df.empty:
                        if selected_industry_idx is not None and selected_industry_idx < len(top_df):
                            selected_industry = top_df.iloc[selected_industry_idx]
                            industry_large = str(selected_industry["IndustryLarge"])
                            industry_mid = str(selected_industry["IndustryMid"])
                            industry_small = str(selected_industry["IndustrySmall"])
                            industry_label = selected_industry["Label"]
                            
                            st.markdown(f"**📊 {industry_label} - 상위 RS 10개 종목**")
                            
                            try:
                                # KRX stock master에서 업종으로 필터링
                                if master_df is not None and not master_df.empty and "Code" in master_df.columns:
                                    master_copy = master_df.copy()
                                    master_copy["Code"] = master_copy["Code"].astype(str)
                                    
                                    # ETF 종목 제외 (업종 강도 계산에서)
                                    if "Market" in master_copy.columns:
                                        master_copy = master_copy[master_copy["Market"] != "ETF"]
                                    
                                    # 업종 필터링 (level에 따라 다르게)
                                    if level == "L":
                                        filtered = master_copy[master_copy["IndustryLarge"] == industry_large]
                                    elif level == "LM":
                                        filtered = master_copy[
                                            (master_copy["IndustryLarge"] == industry_large) &
                                            (master_copy["IndustryMid"] == industry_mid)
                                        ]
                                    else:  # LMS
                                        filtered = master_copy[
                                            (master_copy["IndustryLarge"] == industry_large) &
                                            (master_copy["IndustryMid"] == industry_mid) &
                                            (master_copy["IndustrySmall"] == industry_small)
                                        ]
                                    
                                    tickers_list = filtered["Code"].tolist()
                                    
                                    if tickers_list:
                                        feature_url = feature_asset["browser_download_url"]
                                        tickers_rs_df = query_tickers_rs_by_ticker_list(
                                            feature_url,
                                            tickers_list,
                                            end_d,
                                            limit=10,
                                        )
                                        
                                        if tickers_rs_df is not None and not tickers_rs_df.empty:
                                            # KRX stock master와 조인하여 종목명 추가
                                            tickers_rs_df = tickers_rs_df.copy()
                                            tickers_rs_df["Ticker"] = tickers_rs_df["Ticker"].astype(str)
                                            tickers_rs_df = tickers_rs_df.merge(
                                                master_copy[["Code", "Name", "Market"]],
                                                left_on="Ticker",
                                                right_on="Code",
                                                how="left",
                                            )
                                            tickers_rs_df = tickers_rs_df.drop(columns=["Code"])
                                            display_cols = ["Ticker", "Name", "Market", "MansfieldRS", "Date"]

                                            # 종목 선택 + 선택 종목 차트(종가+RS)
                                            table_df = tickers_rs_df[display_cols].copy()
                                            top10_event = st.dataframe(
                                                table_df,
                                                hide_index=True,
                                                use_container_width=True,
                                                on_select="rerun",
                                                selection_mode="single-row",
                                                key="industry_top10_ticker_df",
                                            )
                                            selected_ticker = None
                                            if getattr(top10_event, "selection", None) and top10_event.selection.rows:
                                                ridx = int(top10_event.selection.rows[0])
                                                if 0 <= ridx < len(table_df):
                                                    selected_ticker = str(table_df.iloc[ridx]["Ticker"])

                                            if selected_ticker:
                                                feature_url = feature_asset["browser_download_url"]
                                                cols = []
                                                try:
                                                    cols = get_parquet_columns(feature_url)
                                                except Exception:
                                                    cols = []

                                                rs_candidates = ["MansfieldRS", "RS", "RelativeStrength"]
                                                rs_col = next((c for c in rs_candidates if c in cols), None)

                                                chart_cols = ["Date", "Ticker", "Close"]
                                                if rs_col:
                                                    chart_cols.append(rs_col)

                                                try:
                                                    ts = query_feature_parquet(
                                                        feature_url,
                                                        selected_ticker,
                                                        start_d,
                                                        end_d,
                                                        tuple(chart_cols),
                                                    )
                                                except Exception as e:
                                                    st.error(f"선택 종목 시계열 조회 실패: {e}")
                                                    ts = pd.DataFrame()

                                                if ts is None or ts.empty:
                                                    st.info("선택한 종목의 데이터가 선택 기간에 없습니다.")
                                                else:
                                                    ts = ts.copy()
                                                    ts["Date"] = _ensure_datetime(ts["Date"])
                                                    ts["Close"] = pd.to_numeric(ts["Close"], errors="coerce")
                                                    if rs_col and rs_col in ts.columns:
                                                        ts[rs_col] = pd.to_numeric(ts[rs_col], errors="coerce")

                                                    st.markdown(f"**📈 `{selected_ticker}` 종가**")
                                                    price_chart = (
                                                        alt.Chart(ts)
                                                        .mark_line()
                                                        .encode(
                                                            x=alt.X("Date:T", title="Date"),
                                                            y=alt.Y("Close:Q", title="Close"),
                                                            tooltip=[
                                                                alt.Tooltip("Date:T"),
                                                                alt.Tooltip("Close:Q"),
                                                            ],
                                                        )
                                                    )
                                                    st.altair_chart(price_chart, use_container_width=True)

                                                    if rs_col and rs_col in ts.columns:
                                                        st.markdown(f"**📉 RS (`{rs_col}`)**")
                                                        rs_base = ts[["Date", rs_col]].copy()
                                                        rs_line = (
                                                            alt.Chart(rs_base)
                                                            .mark_line()
                                                            .encode(
                                                                x=alt.X("Date:T", title="Date"),
                                                                y=alt.Y(f"{rs_col}:Q", title=rs_col),
                                                                tooltip=[
                                                                    alt.Tooltip("Date:T"),
                                                                    alt.Tooltip(f"{rs_col}:Q", title=rs_col),
                                                                ],
                                                            )
                                                        )
                                                        zero = (
                                                            alt.Chart(pd.DataFrame({"y": [0]}))
                                                            .mark_rule(color="#9ca3af", strokeDash=[4, 4])
                                                            .encode(y="y:Q")
                                                        )
                                                        st.altair_chart(alt.layer(rs_line, zero), use_container_width=True)
                                        else:
                                            st.info("선택한 업종에 대한 종목 RS 데이터를 찾을 수 없습니다.")
                                    else:
                                        st.info("선택한 업종에 속하는 종목을 찾을 수 없습니다.")
                                else:
                                    st.warning("KRX stock master가 로드되지 않았습니다. 종목 정보를 조회할 수 없습니다.")
                            except Exception as e:
                                st.error(f"종목 데이터 조회 중 오류 발생: {e}")

            # 4) Chart: search ticker/name and plot selected series
            if feature_asset is not None:
                st.subheader("📈 개별종목 Chart")
                feature_url = feature_asset["browser_download_url"]

                # Ensure KRX stock master is available
                master_df = st.session_state.get("krx_master_df")
                if (master_df is None or master_df.empty) and krx_master_asset is not None:
                    with st.spinner("Loading KRX stock master for market/industry info..."):
                        mdf = load_parquet_from_url(krx_master_asset["browser_download_url"], github_token)
                        if mdf is not None and not mdf.empty:
                            st.session_state["krx_master_df"] = mdf
                            master_df = mdf

                meta_obj = st.session_state.get("meta_obj") or {}
                tickers_in_data = [str(t) for t in (meta_obj.get("tickers") or [])]
                if not tickers_in_data and master_df is not None and "Code" in master_df.columns:
                    tickers_in_data = sorted(master_df["Code"].astype(str).unique().tolist())

                options = None
                if master_df is not None and not master_df.empty and "Code" in master_df.columns:
                    mv = master_df.copy()
                    mv["Code"] = mv["Code"].astype(str)
                    if tickers_in_data:
                        mv = mv[mv["Code"].isin(tickers_in_data)]
                    mv = mv.rename(columns={"Code": "Ticker"})
                    options = mv.to_dict(orient="records")

                if options is not None:
                    search = st.text_input("Search (Ticker or Name)", value="")
                    if search:
                        s = search.strip().lower()
                        options = [
                            o
                            for o in options
                            if s in str(o.get("Ticker", "")).lower() or s in str(o.get("Name", "")).lower()
                        ]
                    selected = st.selectbox(
                        "Select Ticker",
                        options,
                        format_func=lambda o: f"{o.get('Ticker','')} - {o.get('Name','')} ({o.get('Market','')})",
                    )
                    selected_ticker = str(selected.get("Ticker", ""))
                else:
                    st.info("KRX stock master not available. (Ticker-only selection)")
                    search = st.text_input("Search (Ticker)", value="")
                    options2 = tickers_in_data
                    if search:
                        s = search.strip().lower()
                        options2 = [t for t in options2 if s in t.lower()]
                    selected_ticker = st.selectbox("Select Ticker", options2) if options2 else ""

                if not selected_ticker:
                    st.warning("No ticker selected.")
                else:
                    # Show selected ticker market/industry info (if available)
                    if master_df is not None and not master_df.empty and "Code" in master_df.columns:
                        mv = master_df.copy()
                        mv["Code"] = mv["Code"].astype(str)
                        row = mv[mv["Code"] == selected_ticker]
                        if not row.empty:
                            r0 = row.iloc[0].to_dict()
                            st.markdown(
                                f"**Selected**: `{selected_ticker}` - {r0.get('Name','')} "
                                f"(**{r0.get('Market','')}**)\n\n"
                                f"- **Industry (L/M/S)**: {r0.get('IndustryLarge','')} / {r0.get('IndustryMid','')} / {r0.get('IndustrySmall','')}"
                            )

                    try:
                        min_date, max_date = query_feature_date_bounds(feature_url, selected_ticker)
                    except Exception as e:
                        st.error(f"Failed to query date bounds (likely URL/access issue): {e}")
                        min_date, max_date = None, None

                    if min_date is None or max_date is None:
                        st.warning("No data available for selected ticker.")
                    else:
                        min_d = pd.to_datetime(min_date).date()
                        max_d = pd.to_datetime(max_date).date()
                        default_start = max(min_d, (pd.Timestamp(max_d) - pd.Timedelta(days=365)).date())
                        start_d, end_d = st.slider("Date range", min_value=min_d, max_value=max_d, value=(default_start, max_d))

                        show_newhigh = st.checkbox("Show 1Y New High markers", value=False)
                        # Marker position is fixed to Close (UI removed)
                        marker_pos = "Close"

                        tab_line, tab_candle = st.tabs(["Close & Metrics (Line)", "Candlestick (OHLC)"])

                        all_cols = meta_obj.get("columns") or []
                        numeric_candidates = [c for c in all_cols if c not in {"Date", "Ticker"}]

                        with tab_line:
                            extra = st.multiselect(
                                "Additional numeric metrics (Close is always shown)",
                                options=[c for c in numeric_candidates if c != "Close"],
                                default=[],
                            )
                            metrics = ["Close"] + [c for c in extra if c != "Close"]
                            need_cols = tuple(["Date", "Ticker"] + sorted(set(metrics + ["IsNewHigh1Y", marker_pos])))
                            one = query_feature_parquet(feature_url, selected_ticker, start_d, end_d, need_cols)
                            if one.empty:
                                st.warning("No data in selected date range.")
                            else:
                                one["Date"] = _ensure_datetime(one["Date"])
                                marker_y_col = "Close"
                                newhigh_layer = _build_newhigh_marker_layer(one, "Date", marker_y_col) if show_newhigh else None
                                left_cols, right_cols = _axis_assignment(one, "Close", [c for c in metrics if c != "Close"])
                                chart = _build_dual_axis_chart(one, "Date", ["Close"] + [c for c in left_cols if c != "Close"], right_cols, marker_layer=newhigh_layer)
                                st.altair_chart(chart, use_container_width=True)

                        with tab_candle:
                            extra = st.multiselect(
                                "Additional numeric metrics to overlay",
                                options=[c for c in numeric_candidates if c not in {"Open", "High", "Low", "Close"}],
                                default=[],
                                key="candle_extra_metrics",
                            )
                            metrics = [c for c in extra if c != "Close"]
                            need_cols = tuple(["Date", "Ticker", "Open", "High", "Low", "Close"] + sorted(set(metrics + ["IsNewHigh1Y", marker_pos])))
                            one = query_feature_parquet(feature_url, selected_ticker, start_d, end_d, need_cols)
                            if one.empty:
                                st.warning("No data in selected date range.")
                            else:
                                one["Date"] = _ensure_datetime(one["Date"])
                                marker_y_col = "Close"
                                newhigh_layer = _build_newhigh_marker_layer(one, "Date", marker_y_col) if show_newhigh else None
                                candle = _build_candlestick_with_metrics(one, "Date", metrics, marker_layer=newhigh_layer)
                                if candle is None:
                                    st.info("Could not build candlestick chart for this data.")
                                else:
                                    st.altair_chart(candle, use_container_width=True)
    else:
        if repo_name != default_repo:
            st.info("No releases found. Please check the repository name or token.")
else:
    st.info("Please enter a repository name in the sidebar.")
