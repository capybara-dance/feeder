from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import sys
from pathlib import Path

# Ensure repository root is importable when run as a script.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from capybara_fetcher.notifications import TelegramSender
from capybara_fetcher.pipeline import CollectionConfig, collect_data


def _fmt_table(df, max_rows: int = 10) -> str:
    if df is None or df.empty:
        return "<p><i>No rows</i></p>"
    return df.head(max_rows).to_html(index=False, escape=True, border=1)


def _build_html_report(result, cfg: CollectionConfig, started_at: dt.datetime, ended_at: dt.datetime) -> str:
    duration_sec = (ended_at - started_at).total_seconds()

    industry_rows = len(result.industry_df)
    master_rows = len(result.master_df)
    price_rows = len(result.price_df)
    dividend_rows = len(result.dividend_df)

    market_counts = (
        result.master_df["MARKET_CODE"].value_counts(dropna=False).rename_axis("MARKET_CODE").reset_index(name="COUNT")
        if not result.master_df.empty
        else None
    )

    ticker_counts = (
        result.price_df.groupby("TICKER", as_index=False)
        .size()
        .rename(columns={"size": "ROW_COUNT"})
        .sort_values("ROW_COUNT", ascending=False)
        if not result.price_df.empty
        else None
    )

    qm = result.quality_metrics or {}
    price_rows = int(qm.get("price_row_count", price_rows if price_rows else 0))
    missing_before = int(qm.get("market_cap_missing_before", 0))
    missing_after = int(qm.get("market_cap_missing_after_enrichment", 0))
    zero_final = int(qm.get("market_cap_zero_final", 0))
    zero_ratio = (zero_final / price_rows * 100.0) if price_rows > 0 else 0.0

    cap_sample = (
        result.price_df[["TICKER", "PRICE_DATE", "CLOSE_PRICE", "MARKET_CAP"]]
        .sort_values("MARKET_CAP", ascending=False)
        .head(15)
        if not result.price_df.empty
        else None
    )

    return f"""<!doctype html>
<html lang=\"ko\">
<head>
  <meta charset=\"utf-8\" />
  <title>Collection Test Report</title>
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
  <h1>Data Collection Test Report</h1>
  <div class=\"meta\">Generated at: {html.escape(ended_at.isoformat())}</div>

  <div class=\"card\">
    <h2>Run Config</h2>
    <p>
      start_date=<code>{html.escape(cfg.start_date)}</code>,
      end_date=<code>{html.escape(cfg.end_date)}</code>,
      test_limit=<code>{cfg.test_limit}</code>,
      max_workers=<code>{cfg.max_workers}</code>,
      market=<code>{html.escape(str(cfg.market))}</code>
    </p>
    <p>Duration: <b>{duration_sec:.2f}s</b></p>
  </div>

  <div class=\"card\">
    <h2>Result Summary</h2>
    <ul>
      <li>industry_df rows: <b>{industry_rows}</b></li>
      <li>master_df rows: <b>{master_rows}</b></li>
      <li>price_df rows: <b>{price_rows}</b></li>
      <li>dividend_df rows: <b>{dividend_rows}</b></li>
    </ul>
  </div>

  <div class=\"card\">
    <h2>Market Cap Quality</h2>
    <ul>
      <li>market_cap_missing_before: <b>{missing_before}</b></li>

  <div class="card">
    <h2>dividend_df sample</h2>
    {_fmt_table(result.dividend_df, max_rows=15)}
  </div>
      <li>market_cap_missing_after_enrichment: <b>{missing_after}</b></li>
      <li>market_cap_zero_final: <b>{zero_final}</b> ({zero_ratio:.2f}%)</li>
    </ul>
  </div>

  <div class=\"card\">
    <h2>market distribution (master_df)</h2>
    {_fmt_table(market_counts, max_rows=20)}
  </div>

  <div class=\"card\">
    <h2>industry_df sample</h2>
    {_fmt_table(result.industry_df, max_rows=15)}
  </div>

  <div class=\"card\">
    <h2>master_df sample</h2>
    {_fmt_table(result.master_df, max_rows=15)}
  </div>

  <div class=\"card\">
    <h2>price_df sample</h2>
    {_fmt_table(result.price_df, max_rows=15)}
  </div>

  <div class=\"card\">
    <h2>top market cap sample</h2>
    {_fmt_table(cap_sample, max_rows=15)}
  </div>

  <div class=\"card\">
    <h2>top tickers by row count (price_df)</h2>
    {_fmt_table(ticker_counts, max_rows=15)}
  </div>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run collection test, generate HTML report, and send to Telegram")
    parser.add_argument("--start-date", type=str, default=(dt.date.today() - dt.timedelta(days=365)).strftime("%Y-%m-%d"))
    parser.add_argument("--end-date", type=str, default=dt.date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--test-limit", type=int, default=5)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--market", type=str, default=None)
    parser.add_argument("--output-html", type=str, default="reports/collection_test_report.html")
    parser.add_argument("--caption", type=str, default="collection test report")
    parser.add_argument("--no-send", action="store_true", help="Generate report only, do not send to Telegram")
    args = parser.parse_args()

    cfg = CollectionConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        test_limit=args.test_limit,
        max_workers=args.max_workers,
        adjusted=True,
        market=args.market,
    )

    started_at = dt.datetime.now(dt.timezone.utc)
    result = collect_data(cfg)
    ended_at = dt.datetime.now(dt.timezone.utc)

    report_html = _build_html_report(result, cfg, started_at, ended_at)
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
