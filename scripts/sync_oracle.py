from __future__ import annotations

import argparse
import datetime as dt
import html
import logging
import os
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

# Ensure repository root import path when run as script.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.dotenv_loader import load_dotenv_if_present


load_dotenv_if_present(Path(REPO_ROOT) / ".env")

from capybara_fetcher.db import OracleClient, OracleRepository
from capybara_fetcher.notifications import TelegramSender
from capybara_fetcher.pipeline import CollectionConfig, collect_data
from capybara_fetcher.pipeline.release_ingest import (
    estimate_release_batch_count,
    iter_release_price_batches,
    prepare_release_data,
)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logging.raiseExceptions = False
logger = logging.getLogger(__name__)

TABLE_KEY_TO_NAME = {
    "industry": "STOCK_INDUSTRY",
    "master": "STOCK_MASTER",
    "price": "DAILY_PRICE",
    "dividend": "STOCK_DIVIDEND",
}


def _parse_target_tables(raw: str) -> set[str]:
    tokens = [t.strip().lower() for t in str(raw or "all").split(",") if t.strip()]
    if not tokens or "all" in tokens:
        return set(TABLE_KEY_TO_NAME.keys())

    invalid = sorted([t for t in tokens if t not in TABLE_KEY_TO_NAME])
    if invalid:
        valid = ", ".join(["all", *TABLE_KEY_TO_NAME.keys()])
        raise ValueError(f"invalid table selector: {invalid}. valid values: {valid}")
    return set(tokens)


def _iter_with_optional_progress(iterable, *, total: int | None, desc: str, use_tqdm: bool):
    if use_tqdm and tqdm is not None:
        yield from tqdm(iterable, total=total, desc=desc, unit="batch")
        return
    for i, item in enumerate(iterable, start=1):
        if i == 1 or i % 5 == 0:
            if total:
                logger.info("%s progress: batch %s/%s", desc, i, total)
            else:
                logger.info("%s progress: batch %s", desc, i)
        yield item


def _to_iso_date(v: str) -> str:
    s = str(v).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    dt.date.fromisoformat(s)
    return s


def _today_kst() -> dt.date:
    return dt.datetime.now(ZoneInfo("Asia/Seoul")).date()


def _business_days(start_date: dt.date, end_date: dt.date) -> list[dt.date]:
    days: list[dt.date] = []
    cur = start_date
    while cur <= end_date:
        if cur.weekday() < 5:
            days.append(cur)
        cur += dt.timedelta(days=1)
    return days


def _table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM user_tables WHERE table_name = :table_name",
            {"table_name": table_name.upper()},
        )
        row = cur.fetchone()
    return row is not None


def _fetch_one(conn, sql: str, params: dict | None = None):
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchone()


def _db_total_stats() -> dict[str, object]:
    table_names = ["STOCK_INDUSTRY", "STOCK_MASTER", "DAILY_PRICE", "STOCK_DIVIDEND", "ETF_COMPONENT"]
    counts: dict[str, int] = {}
    price_range: tuple[str | None, str | None] = (None, None)

    with OracleClient.from_env(batch_size=1) as client:
        conn = client.connection

        for table in table_names:
            if not _table_exists(conn, table):
                counts[table] = 0
                continue
            row = _fetch_one(conn, f"SELECT COUNT(*) FROM {table}")
            counts[table] = int(row[0]) if row else 0

        if _table_exists(conn, "DAILY_PRICE"):
            row = _fetch_one(conn, "SELECT MIN(TRUNC(PRICE_DATE)), MAX(TRUNC(PRICE_DATE)) FROM DAILY_PRICE")
            if row:
                min_d = row[0].strftime("%Y-%m-%d") if row[0] else None
                max_d = row[1].strftime("%Y-%m-%d") if row[1] else None
                price_range = (min_d, max_d)

    return {
        "counts": counts,
        "price_date_range": price_range,
    }


def _build_html_report(
    *,
    source: str,
    mode: str,
    resolved_start: str,
    resolved_end: str,
    target_dates_count: int | None,
    collect_started_at: dt.datetime,
    collect_ended_at: dt.datetime,
    upsert_started_at: dt.datetime | None,
    upsert_ended_at: dt.datetime | None,
    collection_stats: dict[str, object],
    upsert_stats: dict[str, int],
    db_stats: dict[str, object],
    collect_dividends: bool,
    dry_run: bool,
    run_error: str | None,
    release_info: dict[str, str | None] | None,
) -> str:
    collect_sec = (collect_ended_at - collect_started_at).total_seconds()
    upsert_sec = (upsert_ended_at - upsert_started_at).total_seconds() if upsert_started_at and upsert_ended_at else 0.0

    q = collection_stats.get("quality_metrics", {}) or {}
    db_counts = db_stats.get("counts", {}) or {}
    db_min, db_max = db_stats.get("price_date_range", (None, None))
    status = "실패" if run_error else "성공"
    release_line = ""
    if release_info:
        release_line = (
            f"릴리즈 repo=<code>{html.escape(str(release_info.get('repo') or ''))}</code>, "
            f"tag=<code>{html.escape(str(release_info.get('tag') or ''))}</code>, "
            f"name=<code>{html.escape(str(release_info.get('name') or ''))}</code>, "
            f"published_at=<code>{html.escape(str(release_info.get('published_at') or '-'))}</code>"
        )

    return f"""<!doctype html>
<html lang=\"ko\">
<head>
    <meta charset=\"utf-8\" />
    <title>오라클 동기화 리포트</title>
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
    <h1>오라클 동기화 리포트</h1>
    <div class=\"meta\">생성 시각(UTC): {html.escape(dt.datetime.now(dt.timezone.utc).isoformat())}</div>

    <div class=\"card\">
        <h2>실행 설정</h2>
        <p>
            실행 모드=<code>{html.escape(mode)}</code>,
            입력 소스=<code>{html.escape(source)}</code>,
            수집 시작일=<code>{html.escape(resolved_start)}</code>,
            수집 종료일=<code>{html.escape(resolved_end)}</code>,
            대상 날짜 수=<code>{target_dates_count if target_dates_count is not None else '전체'}</code>,
            배당 수집=<code>{collect_dividends}</code>,
            드라이런=<code>{dry_run}</code>,
            실행 상태=<code>{status}</code>
        </p>
        <p>{release_line if release_line else '<i>release 메타 없음</i>'}</p>
        <p>수집 소요시간=<b>{collect_sec:.2f}초</b>, 업서트 소요시간=<b>{upsert_sec:.2f}초</b></p>
    </div>

    <div class=\"card\">
        <h2>실행 오류</h2>
        <p>{html.escape(run_error) if run_error else '<i>오류 없음</i>'}</p>
    </div>

    <div class=\"card\">
        <h2>수집 통계</h2>
        <ul>
            <li>industry_df 행 수: <b>{collection_stats.get('industry_rows', 0)}</b></li>
            <li>master_df 행 수: <b>{collection_stats.get('master_rows', 0)}</b></li>
            <li>price_df 행 수: <b>{collection_stats.get('price_rows', 0)}</b></li>
            <li>dividend_df 행 수: <b>{collection_stats.get('dividend_rows', 0)}</b></li>
        </ul>
        <ul>
            <li>시가총액 결측(보강 전): <b>{q.get('market_cap_missing_before', 0)}</b></li>
            <li>시가총액 결측(보강 후): <b>{q.get('market_cap_missing_after_enrichment', 0)}</b></li>
            <li>시가총액 0 최종 건수: <b>{q.get('market_cap_zero_final', 0)}</b></li>
            <li>가격 데이터 행 수(metric): <b>{q.get('price_row_count', 0)}</b></li>
            <li>배당 데이터 행 수(metric): <b>{q.get('dividend_row_count', 0)}</b></li>
        </ul>
    </div>

    <div class=\"card\">
        <h2>업서트 통계</h2>
        <ul>
            <li>STOCK_INDUSTRY 업서트 행 수: <b>{upsert_stats.get('STOCK_INDUSTRY', 0)}</b></li>
            <li>STOCK_MASTER 업서트 행 수: <b>{upsert_stats.get('STOCK_MASTER', 0)}</b></li>
            <li>DAILY_PRICE 업서트 행 수: <b>{upsert_stats.get('DAILY_PRICE', 0)}</b></li>
            <li>STOCK_DIVIDEND 업서트 행 수: <b>{upsert_stats.get('STOCK_DIVIDEND', 0)}</b></li>
        </ul>
    </div>

    <div class=\"card\">
        <h2>DB 전체 통계</h2>
        <ul>
            <li>STOCK_INDUSTRY 전체 행 수: <b>{db_counts.get('STOCK_INDUSTRY', 0)}</b></li>
            <li>STOCK_MASTER 전체 행 수: <b>{db_counts.get('STOCK_MASTER', 0)}</b></li>
            <li>DAILY_PRICE 전체 행 수: <b>{db_counts.get('DAILY_PRICE', 0)}</b></li>
            <li>STOCK_DIVIDEND 전체 행 수: <b>{db_counts.get('STOCK_DIVIDEND', 0)}</b></li>
            <li>ETF_COMPONENT 전체 행 수: <b>{db_counts.get('ETF_COMPONENT', 0)}</b></li>
            <li>DAILY_PRICE 날짜 범위: <b>{db_min or '-'} ~ {db_max or '-'}</b></li>
        </ul>
    </div>
</body>
</html>
"""


def _existing_price_dates(*, lookback_start: dt.date, lookback_end: dt.date) -> set[dt.date]:
    existing: set[dt.date] = set()
    with OracleClient.from_env(batch_size=1) as client:
        conn = client.connection
        if not _table_exists(conn, "DAILY_PRICE"):
            return existing

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT TRUNC(PRICE_DATE) AS D
                FROM DAILY_PRICE
                WHERE PRICE_DATE BETWEEN :start_date AND :end_date
                """,
                {
                    "start_date": dt.datetime.combine(lookback_start, dt.time.min),
                    "end_date": dt.datetime.combine(lookback_end, dt.time.max),
                },
            )
            for (d,) in cur.fetchall():
                if hasattr(d, "date"):
                    existing.add(d.date())
                elif isinstance(d, dt.date):
                    existing.add(d)
    return existing


def _resolve_collection_window(*, mode: str, lookback_days: int, start_date: str | None, end_date: str | None) -> tuple[str, str, set[dt.date] | None]:
    today = _today_kst()

    if mode == "full-10y":
        start = today - dt.timedelta(days=3650)
        return start.isoformat(), today.isoformat(), None

    if mode == "range":
        if not start_date or not end_date:
            raise ValueError("mode=range requires --start-date and --end-date")
        s = dt.date.fromisoformat(_to_iso_date(start_date))
        e = dt.date.fromisoformat(_to_iso_date(end_date))
        if s > e:
            raise ValueError("start-date must be <= end-date")
        return s.isoformat(), e.isoformat(), None

    # mode == daily
    lookback_start = today - dt.timedelta(days=int(lookback_days))
    expected = set(_business_days(lookback_start, today))
    target_dates = {today}
    try:
        existing = _existing_price_dates(lookback_start=lookback_start, lookback_end=today)
    except Exception as e:
        logger.warning("Failed to inspect DAILY_PRICE for missing dates; fallback to today only: %s", e)
        existing = set()

    missing = sorted(expected - existing)
    if missing:
        target_dates.update(missing)

    start = min(target_dates)
    end = max(target_dates)
    logger.info("Daily mode target dates count=%s (missing business days in lookback=%s)", len(target_dates), len(missing))
    return start.isoformat(), end.isoformat(), target_dates


def _filter_dates(result, target_dates: set[dt.date] | None):
    if not target_dates:
        return result

    price = result.price_df.copy()
    if not price.empty and "PRICE_DATE" in price.columns:
        d = dt.datetime
        price_dates = price["PRICE_DATE"].apply(lambda x: x.date() if hasattr(x, "date") else d.fromisoformat(str(x)).date())
        price = price[price_dates.isin(target_dates)]

    dividend = result.dividend_df.copy()
    if not dividend.empty and "EX_DIVIDEND_DATE" in dividend.columns:
        dividend_dates = dividend["EX_DIVIDEND_DATE"].apply(
            lambda x: x.date() if hasattr(x, "date") else dt.datetime.fromisoformat(str(x)).date()
        )
        dividend = dividend[dividend_dates.isin(target_dates)]

    from capybara_fetcher.pipeline.collect import CollectionResult

    return CollectionResult(
        industry_df=result.industry_df,
        master_df=result.master_df,
        price_df=price.reset_index(drop=True),
        dividend_df=dividend.reset_index(drop=True),
        quality_metrics=result.quality_metrics,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect data and upsert into OracleDB")
    parser.add_argument("--source", choices=["collect", "release"], default="collect", help="Data source mode")
    parser.add_argument("--mode", choices=["daily", "full-10y", "range"], default="daily")
    parser.add_argument("--lookback-days", type=int, default=10, help="In daily mode, check missing business dates in [today-lookback, today]")
    parser.add_argument("--start-date", type=str, default=None, help="Required when mode=range; format YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--end-date", type=str, default=None, help="Required when mode=range; format YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--test-limit", type=int, default=0)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--market", type=str, default=None, help="Optional market filter, e.g. KOSPI/KOSDAQ/ETF")
    parser.add_argument("--master-json-path", type=str, default=None, help="Optional stock master json path")
    parser.add_argument("--output-html", type=str, default="reports/sync_oracle_report.html")
    parser.add_argument("--caption", type=str, default="sync oracle report")
    parser.add_argument("--no-send-report", action="store_true", help="Generate report only, do not send to Telegram")
    parser.add_argument("--dry-run", action="store_true", help="Collect only and print row counts without DB upsert")
    parser.add_argument("--skip-dividends", action="store_true", help="Skip dividend collection stage")
    parser.add_argument("--release-repo", type=str, default="capybara-dance/capybara_fetcher", help="GitHub release repo owner/name")
    parser.add_argument("--release-tag", type=str, default=None, help="Release tag (default: latest)")
    parser.add_argument("--release-token", type=str, default=None, help="Optional GitHub token for release API/asset download")
    parser.add_argument("--no-progress", action="store_true", help="Disable tqdm progress bar and use plain logs")
    parser.add_argument(
        "--tables",
        type=str,
        default="all",
        help="Comma-separated target tables: all|industry|master|price|dividend",
    )
    args = parser.parse_args()
    target_tables = _parse_target_tables(args.tables)

    resolved_start, resolved_end, target_dates = _resolve_collection_window(
        mode=args.mode,
        lookback_days=int(args.lookback_days),
        start_date=args.start_date,
        end_date=args.end_date,
    )

    logger.info("Resolved collection window: start=%s end=%s mode=%s source=%s", resolved_start, resolved_end, args.mode, args.source)
    logger.info("Target tables: %s", ",".join([TABLE_KEY_TO_NAME[k] for k in sorted(target_tables)]))

    collect_prices = "price" in target_tables
    collect_dividends = ("dividend" in target_tables) and (not bool(args.skip_dividends))

    cfg = CollectionConfig(
        start_date=_to_iso_date(resolved_start),
        end_date=_to_iso_date(resolved_end),
        test_limit=int(args.test_limit),
        max_workers=int(args.max_workers),
        adjusted=True,
        collect_prices=collect_prices,
        collect_dividends=collect_dividends,
        market=args.market,
        master_json_path=args.master_json_path,
    )

    if args.source == "release" and args.mode == "daily":
        raise ValueError("source=release does not support mode=daily yet; use mode=full-10y or mode=range")
    if args.source == "release" and "dividend" in target_tables:
        raise ValueError("source=release does not support STOCK_DIVIDEND yet; remove dividend from --tables")

    release_info: dict[str, str | None] | None = None
    release_prepared = None

    logger.info("Starting collection")
    collect_started_at = dt.datetime.now(dt.timezone.utc)
    run_error: str | None = None
    result = None
    try:
        if args.source == "collect":
            logger.info("Collection source=collect (CompositeProvider)")
            result = collect_data(cfg)
            result = _filter_dates(result, target_dates)
        else:
            logger.info("Collection source=release repo=%s tag=%s", args.release_repo, args.release_tag or "latest")
            release_prepared = prepare_release_data(
                repo=args.release_repo,
                tag=args.release_tag,
                token=(args.release_token or os.getenv("GITHUB_TOKEN")),
            )
            from capybara_fetcher.pipeline.collect import CollectionResult

            result = CollectionResult(
                industry_df=release_prepared.industry_df,
                master_df=release_prepared.master_df,
                price_df=release_prepared.master_df.iloc[0:0][[]].copy(),
                dividend_df=release_prepared.master_df.iloc[0:0][[]].copy(),
                quality_metrics={
                    "market_cap_missing_before": 0,
                    "market_cap_missing_after_enrichment": 0,
                    "market_cap_zero_final": 0,
                    "price_row_count": 0,
                    "dividend_row_count": 0,
                },
            )
            release_info = {
                "repo": release_prepared.release.repo,
                "tag": release_prepared.release.tag,
                "name": release_prepared.release.name,
                "published_at": release_prepared.release.published_at,
            }
    except Exception as e:
        run_error = f"collection failed: {e}"
        logger.exception("Collection failed")
    collect_ended_at = dt.datetime.now(dt.timezone.utc)

    if result is not None:
        logger.info("industry_df: shape=%s", result.industry_df.shape)
        logger.info("master_df: shape=%s", result.master_df.shape)
        logger.info("price_df: shape=%s", result.price_df.shape)
        logger.info("dividend_df: shape=%s", result.dividend_df.shape)

    collection_stats = {
        "industry_rows": int(len(result.industry_df)) if result is not None else 0,
        "master_rows": int(len(result.master_df)) if result is not None else 0,
        "price_rows": int(len(result.price_df)) if result is not None else 0,
        "dividend_rows": int(len(result.dividend_df)) if result is not None else 0,
        "quality_metrics": (result.quality_metrics or {}) if result is not None else {},
    }

    upsert_stats = {
        "STOCK_INDUSTRY": 0,
        "STOCK_MASTER": 0,
        "DAILY_PRICE": 0,
        "STOCK_DIVIDEND": 0,
    }
    upsert_started_at: dt.datetime | None = None
    upsert_ended_at: dt.datetime | None = None

    if args.dry_run:
        logger.info("Dry-run enabled. Skip Oracle upsert.")
        if args.source == "release" and release_prepared is not None and "price" in target_tables:
            use_tqdm = not bool(args.no_progress)
            expected_batches = estimate_release_batch_count(release_prepared, batch_rows=200000)
            started = time.monotonic()
            total_rows = 0
            quality = {
                "market_cap_missing_before": 0,
                "market_cap_missing_after_enrichment": 0,
                "market_cap_zero_final": 0,
                "price_row_count": 0,
                "dropped_unknown_ticker_rows": 0,
            }
            batch_iter = iter_release_price_batches(
                release_prepared,
                start_date=_to_iso_date(resolved_start),
                end_date=_to_iso_date(resolved_end),
                batch_rows=200000,
            )
            for batch_idx, (batch_df, m) in enumerate(
                _iter_with_optional_progress(
                    batch_iter,
                    total=expected_batches if expected_batches > 0 else None,
                    desc="release-dryrun",
                    use_tqdm=use_tqdm,
                ),
                start=1,
            ):
                total_rows += len(batch_df)
                quality["market_cap_missing_before"] += int(m["market_cap_missing_before"])
                quality["market_cap_missing_after_enrichment"] += int(m["market_cap_missing_after_enrichment"])
                quality["market_cap_zero_final"] += int(m["market_cap_zero_final"])
                quality["price_row_count"] += int(m["price_row_count"])
                quality["dropped_unknown_ticker_rows"] += int(m.get("dropped_unknown_ticker_rows", 0))
                if batch_idx == 1 or batch_idx % 5 == 0:
                    elapsed = time.monotonic() - started
                    logger.info(
                        "release dry-run progress: batches=%s rows=%s elapsed=%.1fs",
                        batch_idx,
                        total_rows,
                        elapsed,
                    )
            collection_stats["price_rows"] = int(total_rows)
            collection_stats["quality_metrics"] = {**collection_stats.get("quality_metrics", {}), **quality}
        elif args.source == "release" and release_prepared is not None:
            logger.info("Dry-run release mode: DAILY_PRICE excluded by --tables, skipping price batch scan")
    elif result is None:
        logger.warning("Skipping Oracle upsert because collection failed")
    else:
        logger.info("Starting Oracle upsert")
        upsert_started_at = dt.datetime.now(dt.timezone.utc)
        try:
            with OracleClient.from_env(batch_size=int(args.batch_size)) as client:
                repo = OracleRepository(client)
                if args.source == "release" and release_prepared is not None:
                    use_tqdm = not bool(args.no_progress)
                    if "industry" in target_tables:
                        upsert_stats["STOCK_INDUSTRY"] = int(repo.upsert_stock_industry(result.industry_df))
                    if "master" in target_tables:
                        upsert_stats["STOCK_MASTER"] = int(repo.upsert_stock_master(result.master_df))
                    total_price_rows = 0
                    quality = {
                        "market_cap_missing_before": 0,
                        "market_cap_missing_after_enrichment": 0,
                        "market_cap_zero_final": 0,
                        "price_row_count": 0,
                        "dropped_unknown_ticker_rows": 0,
                    }
                    if "price" in target_tables:
                        expected_batches = estimate_release_batch_count(release_prepared, batch_rows=200000)
                        started = time.monotonic()
                        batch_iter = iter_release_price_batches(
                            release_prepared,
                            start_date=_to_iso_date(resolved_start),
                            end_date=_to_iso_date(resolved_end),
                            batch_rows=200000,
                        )
                        for batch_idx, (batch_df, m) in enumerate(
                            _iter_with_optional_progress(
                                batch_iter,
                                total=expected_batches if expected_batches > 0 else None,
                                desc="release-upsert",
                                use_tqdm=use_tqdm,
                            ),
                            start=1,
                        ):
                            total_price_rows += int(repo.upsert_daily_price(batch_df))
                            quality["market_cap_missing_before"] += int(m["market_cap_missing_before"])
                            quality["market_cap_missing_after_enrichment"] += int(m["market_cap_missing_after_enrichment"])
                            quality["market_cap_zero_final"] += int(m["market_cap_zero_final"])
                            quality["price_row_count"] += int(m["price_row_count"])
                            quality["dropped_unknown_ticker_rows"] += int(m.get("dropped_unknown_ticker_rows", 0))
                            if batch_idx == 1 or batch_idx % 5 == 0:
                                elapsed = time.monotonic() - started
                                logger.info(
                                    "release upsert progress: batches=%s rows=%s elapsed=%.1fs",
                                    batch_idx,
                                    total_price_rows,
                                    elapsed,
                                )
                    else:
                        logger.info("Release upsert: DAILY_PRICE excluded by --tables")

                    upsert_stats["DAILY_PRICE"] = int(total_price_rows)
                    upsert_stats["STOCK_DIVIDEND"] = 0
                    collection_stats["price_rows"] = int(quality["price_row_count"])
                    collection_stats["dividend_rows"] = 0
                    collection_stats["quality_metrics"] = {**collection_stats.get("quality_metrics", {}), **quality}
                    logger.info(
                        "Upsert completed: STOCK_INDUSTRY=%s, STOCK_MASTER=%s, DAILY_PRICE=%s, STOCK_DIVIDEND=%s",
                        upsert_stats["STOCK_INDUSTRY"],
                        upsert_stats["STOCK_MASTER"],
                        upsert_stats["DAILY_PRICE"],
                        upsert_stats["STOCK_DIVIDEND"],
                    )
                else:
                    if "industry" in target_tables:
                        upsert_stats["STOCK_INDUSTRY"] = int(repo.upsert_stock_industry(result.industry_df))
                    if "master" in target_tables:
                        upsert_stats["STOCK_MASTER"] = int(repo.upsert_stock_master(result.master_df))
                    if "price" in target_tables:
                        upsert_stats["DAILY_PRICE"] = int(repo.upsert_daily_price(result.price_df))
                    if "dividend" in target_tables:
                        upsert_stats["STOCK_DIVIDEND"] = int(repo.upsert_stock_dividend(result.dividend_df))

                    logger.info(
                        "Upsert completed: STOCK_INDUSTRY=%s, STOCK_MASTER=%s, DAILY_PRICE=%s, STOCK_DIVIDEND=%s",
                        upsert_stats["STOCK_INDUSTRY"],
                        upsert_stats["STOCK_MASTER"],
                        upsert_stats["DAILY_PRICE"],
                        upsert_stats["STOCK_DIVIDEND"],
                    )
            upsert_ended_at = dt.datetime.now(dt.timezone.utc)
        except Exception as e:
            run_error = (run_error + " | " if run_error else "") + f"upsert failed: {e}"
            logger.exception("Oracle upsert failed")

    try:
        db_stats = _db_total_stats()
    except Exception as e:
        logger.warning("Failed to collect DB total stats: %s", e)
        db_stats = {"counts": {}, "price_date_range": (None, None)}

    report_html = _build_html_report(
        source=args.source,
        mode=args.mode,
        resolved_start=resolved_start,
        resolved_end=resolved_end,
        target_dates_count=(len(target_dates) if target_dates else None),
        collect_started_at=collect_started_at,
        collect_ended_at=collect_ended_at,
        upsert_started_at=upsert_started_at,
        upsert_ended_at=upsert_ended_at,
        collection_stats=collection_stats,
        upsert_stats=upsert_stats,
        db_stats=db_stats,
        collect_dividends=bool(cfg.collect_dividends) if args.source == "collect" else False,
        dry_run=bool(args.dry_run),
        run_error=run_error,
        release_info=release_info,
    )

    output_path = Path(args.output_html)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_html, encoding="utf-8")
    logger.info("Report written: %s", output_path)

    if not args.no_send_report:
        sender = TelegramSender()
        res = sender.send_html_file(str(output_path), caption=args.caption)
        if not res.get("ok"):
            run_error = (run_error + " | " if run_error else "") + f"telegram failed: {res}"
            logger.error("Telegram report send failed: %s", res)
        else:
            logger.info("Telegram report send succeeded")

    if release_prepared is not None:
        release_prepared.cleanup()

    if run_error:
        raise RuntimeError(run_error)


if __name__ == "__main__":
    main()
