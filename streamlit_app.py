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
def load_price_history(ticker: str, years: int = 1) -> pd.DataFrame:
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


def _render_tradingview_widget(ticker: str, price_df: pd.DataFrame) -> None:
        chart_rows: list[dict[str, float | str]] = []
        for row in price_df.itertuples(index=False):
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
        chart_df["ma20"] = chart_df["close"].rolling(window=20, min_periods=20).mean()
        ma20_rows = [
                {"time": row["time"], "value": float(row["ma20"])}
                for _, row in chart_df.dropna(subset=["ma20"])[["time", "ma20"]].iterrows()
        ]

        data_json = json.dumps(chart_rows, ensure_ascii=False)
        ma20_json = json.dumps(ma20_rows, ensure_ascii=False)
        title_json = json.dumps(f"{ticker} - Oracle OHLCV", ensure_ascii=False)

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
                const ma20 = {ma20_json};
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

                const candleSeries = chart.addCandlestickSeries({{
                    upColor: "#DC2626",
                    downColor: "#2563EB",
                    borderVisible: false,
                    wickUpColor: "#DC2626",
                    wickDownColor: "#2563EB",
                }});
                candleSeries.setData(candleData);

                const ma20Series = chart.addLineSeries({{
                    color: "#2563EB",
                    lineWidth: 2,
                    priceLineVisible: false,
                    lastValueVisible: true,
                    title: "MA20",
                }});
                ma20Series.setData(ma20);

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

                chart.timeScale().fitContent();

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
        price_df = load_price_history(selected_ticker, years=1)
    except Exception as exc:
        st.error(f"Failed to query price data from Oracle DB: {exc}")
        return

    if price_df.empty:
        st.warning(f"No price data in last 1 year for ticker: {selected_ticker}")
        return

    st.subheader(f"TradingView Chart ({selected_ticker})")
    st.caption("TradingView Lightweight Charts로 Oracle DB 조회 OHLCV만 렌더링합니다.")
    _render_tradingview_widget(selected_ticker, price_df)

    st.subheader("Recent rows")
    st.dataframe(price_df.tail(30), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
