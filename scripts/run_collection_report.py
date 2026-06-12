from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import sys
from pathlib import Path

import pandas as pd

# Ensure repository root is importable when run as a script.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from capybara_fetcher.notifications import TelegramSender
from capybara_fetcher.db import OracleClient


def _load_dotenv(dotenv_path: str = ".env") -> None:
    """Load simple KEY=VALUE pairs from .env into process env if absent."""
    p = Path(dotenv_path)
    if not p.exists():
        return

    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = raw.strip().strip('"').strip("'")


def _fmt_table(df, max_rows: int = 10) -> str:
    if df is None or df.empty:
        return "<p><i>No rows</i></p>"
    return df.head(max_rows).to_html(index=False, escape=True, border=1)


def _fetch_df(conn, sql: str) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    return pd.DataFrame(rows, columns=cols)


def _table_exists(conn, table_name: str) -> bool:
    q = f"SELECT 1 AS EXISTS_FLAG FROM user_tables WHERE table_name = '{table_name}'"
    return not _fetch_df(conn, q).empty


def _build_html_report(report: dict[str, object], args, started_at: dt.datetime, ended_at: dt.datetime) -> str:
    duration_sec = (ended_at - started_at).total_seconds()

    counts: dict[str, int] = report["counts"]  # type: ignore[assignment]
    samples: dict[str, pd.DataFrame] = report["samples"]  # type: ignore[assignment]

    return f"""<!doctype html>
<html lang=\"ko\">
<head>
  <meta charset=\"utf-8\" />
  <title>Oracle DB Sample Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; margin: 24px; color: #222; }}
    h1, h2 {{ margin: 0 0 12px 0; }}
    .meta {{ margin-bottom: 16px; }}
    .card {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; margin-bottom: 16px; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
    th, td {{ padding: 6px 8px; border: 1px solid #cbd5e1; font-size: 12px; text-align: left; }}
    th {{ background: #e2e8f0; }}
    code {{ background: #eef2ff; padding: 1px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Oracle DB Sample Report</h1>
  <div class=\"meta\">Generated at: {html.escape(ended_at.isoformat())}</div>

  <div class=\"card\">
    <h2>Run Config</h2>
    <p>
      oci_db_user=<code>{html.escape(os.getenv('OCI_DB_USER', ''))}</code>,
      oci_db_dsn=<code>{html.escape(os.getenv('OCI_DB_DSN', ''))}</code>,
      sample_rows=<code>{args.sample_rows}</code>
    </p>
    <p>
      legacy_args(start_date/end_date/test_limit/max_workers/market)=
      <code>{html.escape(str(args.start_date))}</code> /
      <code>{html.escape(str(args.end_date))}</code> /
      <code>{args.test_limit}</code> /
      <code>{args.max_workers}</code> /
      <code>{html.escape(str(args.market))}</code>
    </p>
    <p>Duration: <b>{duration_sec:.2f}s</b></p>
  </div>

  <div class=\"card\">
    <h2>Table Row Counts</h2>
    <ul>
      <li>STOCK_INDUSTRY rows: <b>{counts.get('STOCK_INDUSTRY', 0)}</b></li>
      <li>STOCK_MASTER rows: <b>{counts.get('STOCK_MASTER', 0)}</b></li>
      <li>DAILY_PRICE rows: <b>{counts.get('DAILY_PRICE', 0)}</b></li>
      <li>STOCK_DIVIDEND rows: <b>{counts.get('STOCK_DIVIDEND', 0)}</b></li>
      <li>ETF_COMPONENT rows: <b>{counts.get('ETF_COMPONENT', 0)}</b></li>
    </ul>
  </div>

  <div class=\"card\">
    <h2>STOCK_INDUSTRY sample</h2>
    {_fmt_table(samples.get('STOCK_INDUSTRY'), max_rows=args.sample_rows)}
  </div>

  <div class=\"card\">
    <h2>STOCK_MASTER sample</h2>
    {_fmt_table(samples.get('STOCK_MASTER'), max_rows=args.sample_rows)}
  </div>

  <div class=\"card\">
    <h2>DAILY_PRICE sample</h2>
    {_fmt_table(samples.get('DAILY_PRICE'), max_rows=args.sample_rows)}
  </div>

  <div class=\"card\">
    <h2>STOCK_DIVIDEND sample</h2>
    {_fmt_table(samples.get('STOCK_DIVIDEND'), max_rows=args.sample_rows)}
  </div>

  <div class=\"card\">
    <h2>ETF_COMPONENT sample</h2>
    {_fmt_table(samples.get('ETF_COMPONENT'), max_rows=args.sample_rows)}
  </div>
</body>
</html>
"""


def _collect_db_report(sample_rows: int) -> dict[str, object]:
    table_names = ["STOCK_INDUSTRY", "STOCK_MASTER", "DAILY_PRICE", "STOCK_DIVIDEND", "ETF_COMPONENT"]

    sample_sql = {
        "STOCK_INDUSTRY": f"""
            SELECT INDUSTRY_CODE, LARGE_CLASS, MEDIUM_CLASS, SMALL_CLASS
            FROM STOCK_INDUSTRY
            ORDER BY INDUSTRY_CODE
            FETCH FIRST {int(sample_rows)} ROWS ONLY
        """,
        "STOCK_MASTER": f"""
            SELECT TICKER, STOCK_NAME, MARKET_CODE, ASSET_TYPE, INDUSTRY_CODE, IS_LISTED, UPDATED_AT
            FROM STOCK_MASTER
            ORDER BY UPDATED_AT DESC, TICKER
            FETCH FIRST {int(sample_rows)} ROWS ONLY
        """,
        "DAILY_PRICE": f"""
          SELECT TICKER, PRICE_DATE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, ADJ_CLOSE, VOLUME, MARKET_CAP,
               RS_1M, RS_3M, RS_6M, RS_12M, RS_WEIGHTED
            FROM DAILY_PRICE
            ORDER BY PRICE_DATE DESC, TICKER
            FETCH FIRST {int(sample_rows)} ROWS ONLY
        """,
        "STOCK_DIVIDEND": f"""
            SELECT TICKER, EX_DIVIDEND_DATE, DIVIDEND_PER_SHARE, RECORD_DATE, PAYMENT_DATE, DIVIDEND_TYPE
            FROM STOCK_DIVIDEND
            ORDER BY EX_DIVIDEND_DATE DESC, TICKER
            FETCH FIRST {int(sample_rows)} ROWS ONLY
        """,
        "ETF_COMPONENT": f"""
            SELECT ETF_TICKER, COMPONENT_TICKER, BASE_DATE, WEIGHT_PCT, SHARES_HELD
            FROM ETF_COMPONENT
            ORDER BY BASE_DATE DESC, ETF_TICKER, COMPONENT_TICKER
            FETCH FIRST {int(sample_rows)} ROWS ONLY
        """,
    }

    counts: dict[str, int] = {}
    samples: dict[str, pd.DataFrame] = {}

    with OracleClient.from_env() as client:
        conn = client.connection
        for table in table_names:
            if not _table_exists(conn, table):
                counts[table] = 0
                samples[table] = pd.DataFrame()
                continue

            count_df = _fetch_df(conn, f"SELECT COUNT(*) AS CNT FROM {table}")
            counts[table] = int(count_df.iloc[0]["CNT"]) if not count_df.empty else 0
            samples[table] = _fetch_df(conn, sample_sql[table])

    return {"counts": counts, "samples": samples}


def main() -> None:
    parser = argparse.ArgumentParser(description="Read DB samples, generate HTML report, and send to Telegram")
    # Legacy args are kept for workflow compatibility.
    parser.add_argument("--start-date", type=str, default=(dt.date.today() - dt.timedelta(days=365)).strftime("%Y-%m-%d"))
    parser.add_argument("--end-date", type=str, default=dt.date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--test-limit", type=int, default=5)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--market", type=str, default=None)
    parser.add_argument("--sample-rows", type=int, default=15)
    parser.add_argument("--output-html", type=str, default="reports/collection_test_report.html")
    parser.add_argument("--caption", type=str, default="oracle db sample report")
    parser.add_argument("--no-send", action="store_true", help="Generate report only, do not send to Telegram")
    args = parser.parse_args()

    _load_dotenv(".env")

    started_at = dt.datetime.now(dt.timezone.utc)
    report = _collect_db_report(sample_rows=int(args.sample_rows))
    ended_at = dt.datetime.now(dt.timezone.utc)

    report_html = _build_html_report(report, args, started_at, ended_at)
    output_path = Path(args.output_html)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_html, encoding="utf-8")

    print(f"Report written: {output_path}")

    if not args.no_send:
        sender = TelegramSender()
        res = sender.send_html_file(str(output_path), caption=args.caption)
        if not res.get("ok"):
            raise RuntimeError(f"Telegram send failed: {res}")
        print("Telegram report send succeeded")


if __name__ == "__main__":
    main()
