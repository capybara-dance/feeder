from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from capybara_fetcher.db import OracleClient
from scripts.dotenv_loader import load_dotenv_if_present


REPO_ROOT = Path(__file__).resolve().parent
load_dotenv_if_present(REPO_ROOT / ".env")


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
                    VOLUME
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

    price_df = pd.DataFrame(rows, columns=["PRICE_DATE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE", "VOLUME"])
    if price_df.empty:
        return price_df

    price_df["PRICE_DATE"] = pd.to_datetime(price_df["PRICE_DATE"])
    for col in ["OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE", "VOLUME"]:
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

    resampled = (
        price_df.copy()
        .sort_values("PRICE_DATE")
        .set_index("PRICE_DATE")
        .resample(freq)
        .agg(
            OPEN_PRICE=("OPEN_PRICE", "first"),
            HIGH_PRICE=("HIGH_PRICE", "max"),
            LOW_PRICE=("LOW_PRICE", "min"),
            CLOSE_PRICE=("CLOSE_PRICE", "last"),
            VOLUME=("VOLUME", "sum"),
        )
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


def main() -> None:
    st.set_page_config(page_title="Oracle Price Viewer", layout="wide")

    st.title("Oracle Stock Viewer")
    st.caption("All data is queried from Oracle DB (STOCK_MASTER, DAILY_PRICE).")

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

    st.subheader(f"TradingView Chart ({selected_ticker})")
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
    )


if __name__ == "__main__":
    main()
