from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from streamlit.runtime.scriptrunner import get_script_run_ctx
from streamlit.web import cli as stcli

from capybara_fetcher.db import OracleClient
from scripts.dotenv_loader import load_dotenv_if_present


REPO_ROOT = Path(__file__).resolve().parent
load_dotenv_if_present(REPO_ROOT / ".env")

RS_COLUMNS = ["RS_1M", "RS_3M", "RS_6M", "RS_12M", "RS_WEIGHTED"]


def _run_via_streamlit() -> None:
    sys.argv = ["streamlit", "run", str(Path(__file__).resolve()), *sys.argv[1:]]
    raise SystemExit(stcli.main())


if __name__ == "__main__" and get_script_run_ctx() is None:
    _run_via_streamlit()


TABLE_CONFIGS: list[dict[str, Any]] = [
    {
        "name": "STOCK_MASTER",
        "label": "종목 마스터",
        "description": "티커, 종목명, 시장 구분, 자산 유형 등 종목 기준 정보를 조회합니다.",
        "search_columns": ["TICKER", "STOCK_NAME", "MARKET_CODE", "ASSET_TYPE"],
        "order_by": "UPDATED_AT DESC NULLS LAST, TICKER",
        "date_column": "UPDATED_AT",
    },
    {
        "name": "DAILY_PRICE",
        "label": "일별 시세",
        "description": "Oracle DB에 적재된 일봉 OHLCV, 시가총액, RS 데이터를 최신순으로 조회합니다.",
        "search_columns": ["TICKER"],
        "order_by": "PRICE_DATE DESC, TICKER",
        "date_column": "PRICE_DATE",
    },
    {
        "name": "STOCK_DIVIDEND",
        "label": "배당 내역",
        "description": "종목별 배당락일, 배당금, 지급일 등 배당 정보를 조회합니다.",
        "search_columns": ["TICKER", "DIVIDEND_TYPE"],
        "order_by": "EX_DIVIDEND_DATE DESC, TICKER",
        "date_column": "EX_DIVIDEND_DATE",
    },
    {
        "name": "ETF_COMPONENT",
        "label": "ETF 구성 종목",
        "description": "ETF 기준 편입 종목과 비중, 기준일 데이터를 조회합니다.",
        "search_columns": ["ETF_TICKER", "COMPONENT_TICKER"],
        "order_by": "BASE_DATE DESC, ETF_TICKER, COMPONENT_TICKER",
        "date_column": "BASE_DATE",
    },
    {
        "name": "STOCK_INDUSTRY",
        "label": "업종 마스터",
        "description": "산업 분류 대/중/소 업종 코드와 설명 정보를 조회합니다.",
        "search_columns": ["INDUSTRY_CODE", "LARGE_CLASS", "MEDIUM_CLASS", "SMALL_CLASS"],
        "order_by": "INDUSTRY_CODE",
        "date_column": None,
    },
]
TABLE_CONFIG_MAP = {config["name"]: config for config in TABLE_CONFIGS}


def _get_table_config(table_name: str) -> dict[str, Any]:
    config = TABLE_CONFIG_MAP.get(table_name)
    if config is None:
        raise ValueError(f"Unsupported table: {table_name}")
    return config


def _coerce_display_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    display_df = df.copy()
    for column in display_df.columns:
        if any(token in column for token in ["DATE", "_AT"]):
            display_df[column] = pd.to_datetime(display_df[column], errors="coerce")
    return display_df


@st.cache_data(ttl=300)
def get_table_schema(table_name: str) -> pd.DataFrame:
    _get_table_config(table_name)

    with OracleClient.from_env(batch_size=500) as client:
        conn = client.connection
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COLUMN_NAME, DATA_TYPE, NULLABLE
                FROM USER_TAB_COLUMNS
                WHERE TABLE_NAME = :table_name
                ORDER BY COLUMN_ID
                """,
                {"table_name": table_name.upper()},
            )
            rows = cur.fetchall()

    return pd.DataFrame(rows, columns=["COLUMN_NAME", "DATA_TYPE", "NULLABLE"])


@st.cache_data(ttl=300)
def get_table_stats(table_name: str) -> dict[str, Any]:
    config = _get_table_config(table_name)
    date_column = config.get("date_column")
    date_select = ""
    if date_column:
        date_select = f", MIN({date_column}) AS MIN_DATE, MAX({date_column}) AS MAX_DATE"

    query = f"SELECT COUNT(*) AS ROW_COUNT{date_select} FROM {table_name}"

    with OracleClient.from_env(batch_size=500) as client:
        conn = client.connection
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()

    stats: dict[str, Any] = {"row_count": int(row[0]) if row else 0}
    if date_column:
        stats["min_date"] = row[1] if row and len(row) > 1 else None
        stats["max_date"] = row[2] if row and len(row) > 2 else None
    return stats


@st.cache_data(ttl=120)
def load_table_preview(table_name: str, limit: int = 100, keyword: str = "") -> pd.DataFrame:
    config = _get_table_config(table_name)
    limit_value = max(1, int(limit))
    keyword_value = keyword.strip().upper()
    search_columns = config.get("search_columns") or []

    filters: list[str] = []
    params: dict[str, Any] = {"limit": limit_value}
    if keyword_value and search_columns:
        search_clauses = [f"UPPER({column}) LIKE :keyword_like" for column in search_columns]
        filters.append("(" + " OR ".join(search_clauses) + ")")
        params["keyword_like"] = f"%{keyword_value}%"

    where_clause = f" WHERE {' AND '.join(filters)}" if filters else ""
    query = (
        f"SELECT * FROM ("
        f" SELECT * FROM {table_name}{where_clause}"
        f" ORDER BY {config['order_by']}"
        f") WHERE ROWNUM <= :limit"
    )

    with OracleClient.from_env(batch_size=500) as client:
        conn = client.connection
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            columns = [description[0] for description in cur.description]

    return _coerce_display_df(pd.DataFrame(rows, columns=columns))


@st.cache_data(ttl=300)
def search_tickers(keyword: str, limit: int = 200) -> pd.DataFrame:
    kw = keyword.strip()

    with OracleClient.from_env(batch_size=500) as client:
        conn = client.connection
        with conn.cursor() as cur:
            if kw:
                cur.execute(
                    """
                    SELECT TICKER, STOCK_NAME, MARKET_CODE, ASSET_TYPE
                    FROM (
                        SELECT
                            TICKER,
                            STOCK_NAME,
                            MARKET_CODE,
                            ASSET_TYPE,
                            ROW_NUMBER() OVER (
                                ORDER BY
                                    CASE
                                        WHEN TICKER = :kw_exact THEN 0
                                        WHEN UPPER(STOCK_NAME) = UPPER(:kw_exact) THEN 1
                                        ELSE 2
                                    END,
                                    TICKER
                            ) AS RN
                        FROM STOCK_MASTER
                        WHERE IS_LISTED = 'Y'
                          AND (
                                TICKER LIKE :kw_like
                                OR UPPER(STOCK_NAME) LIKE UPPER(:kw_like)
                          )
                    )
                    WHERE RN <= :limit
                    """,
                    {
                        "kw_exact": kw,
                        "kw_like": f"%{kw}%",
                        "limit": int(limit),
                    },
                )
            else:
                cur.execute(
                    """
                    SELECT TICKER, STOCK_NAME, MARKET_CODE, ASSET_TYPE
                    FROM (
                        SELECT
                            TICKER,
                            STOCK_NAME,
                            MARKET_CODE,
                            ASSET_TYPE,
                            ROW_NUMBER() OVER (
                                ORDER BY
                                    CASE WHEN TICKER = '069500' THEN 0 ELSE 1 END,
                                    TICKER
                            ) AS RN
                        FROM STOCK_MASTER
                        WHERE IS_LISTED = 'Y'
                    )
                    WHERE RN <= :limit
                    """,
                    {"limit": int(limit)},
                )

            rows = cur.fetchall()

    return pd.DataFrame(rows, columns=["TICKER", "STOCK_NAME", "MARKET_CODE", "ASSET_TYPE"])


@st.cache_data(ttl=300)
def load_price_history(ticker: str, years: int = 5) -> pd.DataFrame:
    start_date = dt.datetime.now() - dt.timedelta(days=365 * years)

    with OracleClient.from_env(batch_size=500) as client:
        conn = client.connection
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    TRUNC(PRICE_DATE) AS PRICE_DATE,
                    OPEN_PRICE,
                    HIGH_PRICE,
                    LOW_PRICE,
                    CLOSE_PRICE,
                                        VOLUME,
                                        RS_1M,
                                        RS_3M,
                                        RS_6M,
                                        RS_12M,
                                        RS_WEIGHTED
                FROM DAILY_PRICE
                WHERE TICKER = :ticker
                  AND PRICE_DATE >= :start_date
                ORDER BY PRICE_DATE
                """,
                {
                    "ticker": ticker,
                    "start_date": start_date,
                },
            )
            rows = cur.fetchall()

    price_df = pd.DataFrame(
        rows,
        columns=[
            "PRICE_DATE",
            "OPEN_PRICE",
            "HIGH_PRICE",
            "LOW_PRICE",
            "CLOSE_PRICE",
            "VOLUME",
            "RS_1M",
            "RS_3M",
            "RS_6M",
            "RS_12M",
            "RS_WEIGHTED",
        ],
    )
    if price_df.empty:
        return price_df

    price_df["PRICE_DATE"] = pd.to_datetime(price_df["PRICE_DATE"])
    for col in ["OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE", "VOLUME", *RS_COLUMNS]:
        price_df[col] = pd.to_numeric(price_df[col], errors="coerce")

    return price_df


def _resample_price_history(price_df: pd.DataFrame, interval: str) -> pd.DataFrame:
    if price_df.empty:
        return price_df

    interval = interval.lower()
    if interval == "daily":
        return price_df.copy()

    freq_map = {
        "weekly": "W-FRI",
        "monthly": "M",
    }
    freq = freq_map.get(interval)
    if freq is None:
        return price_df.copy()

    agg_map: dict[str, tuple[str, str]] = {
        "OPEN_PRICE": ("OPEN_PRICE", "first"),
        "HIGH_PRICE": ("HIGH_PRICE", "max"),
        "LOW_PRICE": ("LOW_PRICE", "min"),
        "CLOSE_PRICE": ("CLOSE_PRICE", "last"),
        "VOLUME": ("VOLUME", "sum"),
    }
    for col in RS_COLUMNS:
        if col in price_df.columns:
            agg_map[col] = (col, "last")

    resampled = (
        price_df.copy()
        .sort_values("PRICE_DATE")
        .set_index("PRICE_DATE")
        .resample(freq)
        .agg(**agg_map)
        .dropna(subset=["OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE"])
        .reset_index()
    )
    return resampled


def _render_tradingview_widget(
    ticker: str,
    price_df: pd.DataFrame,
    interval: str,
    chart_type: str,
    atr_period: int,
    chandelier_period: int,
    chandelier_mult: float,
    ma_periods: list[int],
    show_ch_long: bool,
    show_ch_short: bool,
    rs_column: str | None,
) -> None:
    chart_df_raw = _resample_price_history(price_df, interval)

    chart_rows: list[dict[str, float | str]] = []
    for row in chart_df_raw.itertuples(index=False):
        if pd.isna(row.OPEN_PRICE) or pd.isna(row.HIGH_PRICE) or pd.isna(row.LOW_PRICE) or pd.isna(row.CLOSE_PRICE):
            continue

        volume_value = float(row.VOLUME) if not pd.isna(row.VOLUME) else 0.0
        chart_rows.append(
            {
                "time": pd.Timestamp(row.PRICE_DATE).strftime("%Y-%m-%d"),
                "open": float(row.OPEN_PRICE),
                "high": float(row.HIGH_PRICE),
                "low": float(row.LOW_PRICE),
                "close": float(row.CLOSE_PRICE),
                "volume": volume_value,
            }
        )

    if not chart_rows:
        st.warning("차트 렌더링에 사용할 OHLC 데이터가 없습니다.")
        return

    rs_rows: list[dict[str, float | str]] = []
    if rs_column and rs_column in chart_df_raw.columns:
        for row in chart_df_raw[["PRICE_DATE", rs_column]].dropna(subset=[rs_column]).itertuples(index=False):
            rs_rows.append(
                {
                    "time": pd.Timestamp(row.PRICE_DATE).strftime("%Y-%m-%d"),
                    "value": float(getattr(row, rs_column)),
                }
            )

    chart_df = pd.DataFrame(chart_rows)
    latest_price_date = pd.to_datetime(chart_df["time"]).max()
    initial_visible_start = (latest_price_date - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    initial_visible_end = latest_price_date.strftime("%Y-%m-%d")
    ma_rows_map: dict[str, list[dict[str, float | str]]] = {}
    for period in ma_periods:
        ma_col = f"ma_{period}"
        chart_df[ma_col] = chart_df["close"].rolling(window=period, min_periods=period).mean()
        ma_rows_map[str(period)] = [
            {"time": row["time"], "value": float(row[ma_col])}
            for _, row in chart_df.dropna(subset=[ma_col])[["time", ma_col]].iterrows()
        ]

    prev_close = chart_df["close"].shift(1)
    tr_components = pd.concat(
        [
            chart_df["high"] - chart_df["low"],
            (chart_df["high"] - prev_close).abs(),
            (chart_df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    chart_df["tr"] = tr_components.max(axis=1)
    chart_df["atr"] = chart_df["tr"].ewm(alpha=1 / atr_period, adjust=False, min_periods=atr_period).mean()
    chart_df["highest_high"] = chart_df["high"].rolling(window=chandelier_period, min_periods=chandelier_period).max()
    chart_df["lowest_low"] = chart_df["low"].rolling(window=chandelier_period, min_periods=chandelier_period).min()
    chart_df["chandelier_exit_long"] = chart_df["highest_high"] - (chart_df["atr"] * chandelier_mult)
    chart_df["chandelier_exit_short"] = chart_df["lowest_low"] + (chart_df["atr"] * chandelier_mult)

    chandelier_long_rows = [
        {"time": row["time"], "value": float(row["chandelier_exit_long"])}
        for _, row in chart_df.dropna(subset=["chandelier_exit_long"])[["time", "chandelier_exit_long"]].iterrows()
    ]
    chandelier_short_rows = [
        {"time": row["time"], "value": float(row["chandelier_exit_short"])}
        for _, row in chart_df.dropna(subset=["chandelier_exit_short"])[["time", "chandelier_exit_short"]].iterrows()
    ]

    data_json = json.dumps(chart_rows, ensure_ascii=False)
    ma_rows_map_json = json.dumps(ma_rows_map, ensure_ascii=False)
    ma_periods_json = json.dumps(ma_periods, ensure_ascii=False)
    ma_color_map_json = json.dumps(
        {
            "5": "#EF4444",
            "10": "#F97316",
            "20": "#2563EB",
            "60": "#16A34A",
            "120": "#7C3AED",
            "200": "#334155",
        },
        ensure_ascii=False,
    )
    chandelier_long_json = json.dumps(chandelier_long_rows, ensure_ascii=False)
    chandelier_short_json = json.dumps(chandelier_short_rows, ensure_ascii=False)
    title_json = json.dumps(f"{ticker} - Oracle OHLCV", ensure_ascii=False)
    chandelier_long_title_json = json.dumps(f"CH Long({chandelier_period},{chandelier_mult:g})", ensure_ascii=False)
    chandelier_short_title_json = json.dumps(f"CH Short({chandelier_period},{chandelier_mult:g})", ensure_ascii=False)
    show_ch_long_json = json.dumps(show_ch_long)
    show_ch_short_json = json.dumps(show_ch_short)
    rs_rows_json = json.dumps(rs_rows, ensure_ascii=False)
    rs_label_json = json.dumps(rs_column or "", ensure_ascii=False)

    widget_html = f"""
        <div style="width:100%; border:1px solid #E5E7EB; border-radius:8px; overflow:hidden; background:#fff;">
            <div id="tv_lw_root" style="width:100%; aspect-ratio:6 / 4;"></div>
        </div>

        <script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
        <script>
            (function() {{
                const root = document.getElementById("tv_lw_root");
                if (!root || typeof LightweightCharts === "undefined") return;

                const data = {data_json};
                const maRowsMap = {ma_rows_map_json};
                const maPeriods = {ma_periods_json};
                const maColorMap = {ma_color_map_json};
                const chandelierLong = {chandelier_long_json};
                const chandelierShort = {chandelier_short_json};
                const chandelierLongTitle = {chandelier_long_title_json};
                const chandelierShortTitle = {chandelier_short_title_json};
                const showChLong = {show_ch_long_json};
                const showChShort = {show_ch_short_json};
                const rsRows = {rs_rows_json};
                const rsLabel = {rs_label_json};
                const candleData = data.map((d) => ({{
                    time: d.time,
                    open: Number(d.open),
                    high: Number(d.high),
                    low: Number(d.low),
                    close: Number(d.close),
                }}));
                const volumeData = data.map((d) => ({{
                    time: d.time,
                    value: Number(d.volume),
                    color: Number(d.close) >= Number(d.open) ? "rgba(220,38,38,0.45)" : "rgba(37,99,235,0.45)",
                }}));

                const chart = LightweightCharts.createChart(root, {{
                    width: root.clientWidth || 1000,
                    height: root.clientHeight || 667,
                    layout: {{
                        background: {{ type: "solid", color: "#FFFFFF" }},
                        textColor: "#1F2937",
                    }},
                    grid: {{
                        vertLines: {{ color: "#F3F4F6" }},
                        horzLines: {{ color: "#F3F4F6" }},
                    }},
                    rightPriceScale: {{ borderColor: "#E5E7EB" }},
                    timeScale: {{ borderColor: "#E5E7EB", timeVisible: true }},
                }});

                const chartType = {json.dumps(chart_type)};
                if (chartType === "bar") {{
                    const barSeries = chart.addBarSeries({{
                        upColor: "#DC2626",
                        downColor: "#2563EB",
                    }});
                    barSeries.setData(candleData.map((d) => ({{
                        time: d.time,
                        open: d.open,
                        high: d.high,
                        low: d.low,
                        close: d.close,
                    }})));
                }} else {{
                    const candleSeries = chart.addCandlestickSeries({{
                        upColor: "#DC2626",
                        downColor: "#2563EB",
                        borderVisible: false,
                        wickUpColor: "#DC2626",
                        wickDownColor: "#2563EB",
                    }});
                    candleSeries.setData(candleData);
                }}

                maPeriods.forEach((period) => {{
                    const key = String(period);
                    const maRows = maRowsMap[key] || [];
                    if (maRows.length === 0) return;

                    const maSeries = chart.addLineSeries({{
                        color: maColorMap[key] || "#2563EB",
                        lineWidth: 2,
                        priceLineVisible: false,
                        lastValueVisible: true,
                        title: `MA${{key}}`,
                    }});
                    maSeries.setData(maRows);
                }});

                if (showChLong && chandelierLong.length > 0) {{
                    const chandelierLongSeries = chart.addLineSeries({{
                        color: "#F97316",
                        lineWidth: 2,
                        lineStyle: 2,
                        priceLineVisible: false,
                        lastValueVisible: true,
                        title: chandelierLongTitle,
                    }});
                    chandelierLongSeries.setData(chandelierLong);
                }}

                if (showChShort && chandelierShort.length > 0) {{
                    const chandelierShortSeries = chart.addLineSeries({{
                        color: "#7C3AED",
                        lineWidth: 2,
                        lineStyle: 2,
                        priceLineVisible: false,
                        lastValueVisible: true,
                        title: chandelierShortTitle,
                    }});
                    chandelierShortSeries.setData(chandelierShort);
                }}

                if (rsRows.length > 0) {{
                    const rsSeries = chart.addLineSeries({{
                        color: "#B45309",
                        lineWidth: 2,
                        lineStyle: 0,
                        priceLineVisible: false,
                        lastValueVisible: true,
                        title: rsLabel,
                        priceScaleId: "left",
                    }});
                    rsSeries.setData(rsRows);
                    chart.priceScale("left").applyOptions({{
                        visible: true,
                        borderColor: "#E5E7EB",
                        scaleMargins: {{ top: 0.05, bottom: 0.82 }},
                    }});
                }}

                const volumeSeries = chart.addHistogramSeries({{
                    priceFormat: {{ type: "volume" }},
                    priceScaleId: "",
                }});
                volumeSeries.priceScale().applyOptions({{
                    scaleMargins: {{ top: 0.78, bottom: 0 }},
                }});
                volumeSeries.setData(volumeData);

                chart.applyOptions({{
                    watermark: {{
                        visible: true,
                        text: {title_json},
                        color: "rgba(31,41,55,0.14)",
                        fontSize: 18,
                        horzAlign: "left",
                        vertAlign: "top",
                    }},
                }});

                chart.timeScale().setVisibleRange({{
                    from: {json.dumps(initial_visible_start)},
                    to: {json.dumps(initial_visible_end)},
                }});

                if (typeof ResizeObserver !== "undefined") {{
                    const ro = new ResizeObserver(() => {{
                        chart.applyOptions({{
                            width: root.clientWidth || 1000,
                            height: root.clientHeight || 667,
                        }});
                    }});
                    ro.observe(root);
                }}
            }})();
        </script>
        """
    components.html(widget_html, height=900)


def _render_table_tab(table_name: str) -> None:
    config = _get_table_config(table_name)

    st.subheader(f"{table_name}")
    st.caption(config["description"])

    try:
        stats = get_table_stats(table_name)
    except Exception as exc:
        st.error(f"Failed to query table stats from Oracle DB: {exc}")
        return

    metric_columns = st.columns(3)
    metric_columns[0].metric("Rows", f"{stats['row_count']:,}")
    metric_columns[1].metric("Latest Date", str(stats.get("max_date") or "-"))
    metric_columns[2].metric("Earliest Date", str(stats.get("min_date") or "-"))

    with st.expander("컬럼 정보 보기"):
        try:
            schema_df = get_table_schema(table_name)
            st.dataframe(schema_df, width="stretch", hide_index=True)
        except Exception as exc:
            st.error(f"Failed to query schema from Oracle DB: {exc}")

    control_col1, control_col2 = st.columns([2, 1])
    with control_col1:
        keyword = st.text_input(
            "검색 키워드",
            key=f"keyword_{table_name}",
            placeholder="티커, 종목명, 업종코드 등",
        )
    with control_col2:
        limit = st.number_input(
            "미리보기 행 수",
            min_value=10,
            max_value=1000,
            value=100,
            step=10,
            key=f"limit_{table_name}",
        )

    try:
        preview_df = load_table_preview(table_name, limit=int(limit), keyword=keyword)
    except Exception as exc:
        st.error(f"Failed to query table rows from Oracle DB: {exc}")
        return

    if preview_df.empty:
        st.info("조건에 맞는 데이터가 없습니다.")
        return

    st.dataframe(preview_df, width="stretch", hide_index=True)


def _render_chart_tab() -> None:
    st.subheader("개별 종목 차트")
    st.caption("Oracle DB의 STOCK_MASTER와 DAILY_PRICE를 사용해 종목 차트를 조회합니다.")

    keyword = st.text_input("Search by ticker or stock name", placeholder="ex) 005930 or Samsung")

    try:
        ticker_df = search_tickers(keyword=keyword, limit=200)
    except Exception as exc:
        st.error(f"Failed to query tickers from Oracle DB: {exc}")
        return

    if ticker_df.empty:
        st.warning("No ticker found. Try a different keyword.")
        return

    ticker_df = ticker_df.copy()
    ticker_df["DISPLAY"] = ticker_df.apply(
        lambda row: f"{row['TICKER']} | {row['STOCK_NAME']} ({row['MARKET_CODE']}, {row['ASSET_TYPE']})",
        axis=1,
    )

    default_ticker = "069500"
    default_candidates = ticker_df.index[ticker_df["TICKER"].astype(str) == default_ticker].tolist()
    default_index = default_candidates[0] if default_candidates else ticker_df.index[0]

    selected_idx = st.selectbox(
        "Select a ticker",
        options=ticker_df.index.tolist(),
        index=ticker_df.index.tolist().index(default_index),
        format_func=lambda idx: ticker_df.loc[idx, "DISPLAY"],
    )
    selected_ticker = str(ticker_df.loc[selected_idx, "TICKER"])

    try:
        price_df = load_price_history(selected_ticker, years=5)
    except Exception as exc:
        st.error(f"Failed to query price data from Oracle DB: {exc}")
        return

    if price_df.empty:
        st.warning(f"No price data in last 5 years for ticker: {selected_ticker}")
        return

    interval_labels = {"일봉": "daily", "주봉": "weekly", "월봉": "monthly"}
    selected_interval_label = st.radio("Chart Interval", list(interval_labels.keys()), horizontal=True, index=0)
    selected_interval = interval_labels[selected_interval_label]

    option_col1, option_col2, option_col3 = st.columns(3)
    with option_col1:
        chandelier_period = st.number_input("CH Period", min_value=5, max_value=120, value=22, step=1)
    with option_col2:
        atr_period = st.number_input("ATR Period", min_value=5, max_value=120, value=22, step=1)
    with option_col3:
        chandelier_mult = st.number_input("ATR Mult", min_value=0.5, max_value=10.0, value=2.0, step=0.1)

    ma_period_options = [5, 10, 20, 60, 120, 200]
    ma_periods = st.multiselect(
        "MA Lines",
        options=ma_period_options,
        default=[20],
    )

    rs_options = {
        "표시 안함": None,
        "RS 1M": "RS_1M",
        "RS 3M": "RS_3M",
        "RS 6M": "RS_6M",
        "RS 12M": "RS_12M",
        "Weighted RS": "RS_WEIGHTED",
    }
    rs_label = st.selectbox("RS Line", options=list(rs_options.keys()), index=0)
    selected_rs_column = rs_options[rs_label]
    if selected_rs_column and selected_rs_column in price_df.columns and price_df[selected_rs_column].notna().sum() == 0:
        st.warning(f"선택한 RS 컬럼({selected_rs_column})은 값이 없어 라인을 표시할 수 없습니다.")

    ch_line_options = st.multiselect(
        "Chandelier Lines",
        options=["Long", "Short"],
        default=["Long"],
    )
    show_ch_long = "Long" in ch_line_options
    show_ch_short = "Short" in ch_line_options

    chart_style_options = {"캔들": "candle", "바(시고저종)": "bar"}
    selected_chart_style_label = st.radio("Chart Style", list(chart_style_options.keys()), horizontal=True, index=0)
    selected_chart_style = chart_style_options[selected_chart_style_label]

    st.caption("TradingView Lightweight Charts로 Oracle DB 조회 OHLCV만 렌더링합니다.")
    _render_tradingview_widget(
        selected_ticker,
        price_df,
        interval=selected_interval,
        chart_type=selected_chart_style,
        atr_period=int(atr_period),
        chandelier_period=int(chandelier_period),
        chandelier_mult=float(chandelier_mult),
        ma_periods=[int(period) for period in ma_periods],
        show_ch_long=show_ch_long,
        show_ch_short=show_ch_short,
        rs_column=selected_rs_column,
    )


def main() -> None:
    st.set_page_config(page_title="Oracle Price Viewer", layout="wide")

    st.title("Oracle Stock Viewer")
    st.caption("Oracle DB 테이블별로 기능을 선택할 수 있습니다. 차트 조회와 테이블 브라우저를 탭으로 분리했습니다.")

    tab_labels = ["개별 종목 차트"] + [config["name"] for config in TABLE_CONFIGS]
    tabs = st.tabs(tab_labels)

    with tabs[0]:
        _render_chart_tab()

    for index, config in enumerate(TABLE_CONFIGS, start=1):
        with tabs[index]:
            _render_table_tab(config["name"])


if __name__ == "__main__":
    main()
