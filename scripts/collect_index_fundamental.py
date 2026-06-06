"""지수 기초 지표(PER/PBR/배당수익률) 수집 및 텔레그램 전송 스크립트.

pykrx stock.get_index_fundamental() 를 사용해 지정 기간의 지수 기초 지표를 수집하고
결과를 텔레그램으로 전송합니다.

사용 예시:
    python scripts/collect_index_fundamental.py \\
        --start-date 20240101 \\
        --end-date 20240131 \\
        --index-codes 1001,2001 \\
        --no-send
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pandas as pd

from capybara_fetcher.providers import CompositeProvider
from capybara_fetcher.notifications import TelegramSender

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# 주요 지수 코드 → 표시 이름 매핑
_INDEX_NAMES: dict[str, str] = {
    "1001": "KOSPI",
    "2001": "KOSDAQ",
    "1028": "KOSPI 200",
    "1003": "KRX 100",
}


def _to_iso_date(v: str) -> str:
    s = str(v).strip().replace("-", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    dt.date.fromisoformat(s)
    return s


def _format_number(val: object, decimals: int = 2) -> str:
    try:
        f = float(val)  # type: ignore[arg-type]
        return f"{f:,.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def _build_text_report(
    results: dict[str, pd.DataFrame],
    start_date: str,
    end_date: str,
) -> str:
    """지수별 기초 지표 결과를 텔레그램 텍스트 포맷으로 변환합니다."""
    lines: list[str] = []
    lines.append("📊 <b>지수 기초 지표 (배당수익률 포함)</b>")
    lines.append(f"기간: {start_date} ~ {end_date}\n")

    for code, df in results.items():
        label = _INDEX_NAMES.get(code, code)
        lines.append(f"<b>▶ {label} ({code})</b>")

        if df is None or df.empty:
            lines.append("  데이터 없음\n")
            continue

        # 날짜 인덱스 정규화
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.copy()
            df.index = df.index.normalize()

        # 배당수익률 컬럼 탐색 (pykrx 버전에 따라 컬럼명 다를 수 있음)
        div_col = None
        for c in df.columns:
            if "배당" in str(c):
                div_col = c
                break

        per_col = next((c for c in df.columns if "PER" in str(c).upper()), None)
        pbr_col = next((c for c in df.columns if "PBR" in str(c).upper()), None)

        # 전체 기간 마지막 행 요약
        last = df.iloc[-1]
        first = df.iloc[0]

        first_date = df.index[0].strftime("%Y-%m-%d") if hasattr(df.index[0], "strftime") else str(df.index[0])
        last_date = df.index[-1].strftime("%Y-%m-%d") if hasattr(df.index[-1], "strftime") else str(df.index[-1])

        if div_col:
            div_first = _format_number(first.get(div_col))
            div_last = _format_number(last.get(div_col))
            lines.append(f"  배당수익률({div_col}): {div_first}% → {div_last}%")
            div_avg = _format_number(pd.to_numeric(df[div_col], errors="coerce").mean())
            lines.append(f"  기간 평균 배당수익률: {div_avg}%")

        if per_col:
            lines.append(f"  PER: {_format_number(first.get(per_col))} → {_format_number(last.get(per_col))}")

        if pbr_col:
            lines.append(f"  PBR: {_format_number(first.get(pbr_col))} → {_format_number(last.get(pbr_col))}")

        lines.append(f"  조회기간: {first_date} ~ {last_date} ({len(df)}일)")
        lines.append(f"  컬럼: {', '.join(df.columns.tolist())}\n")

    lines.append("✅ 수집 완료")
    return "\n".join(lines)


def _build_detail_text(code: str, df: pd.DataFrame) -> str:
    """지수 하나의 전체 일별 데이터를 텍스트로 변환합니다 (상위 20행)."""
    label = _INDEX_NAMES.get(code, code)
    lines = [f"<b>📋 {label} ({code}) 일별 상세</b>"]

    if df is None or df.empty:
        lines.append("데이터 없음")
        return "\n".join(lines)

    if isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df.index = df.index.normalize()

    display = df.tail(20) if len(df) > 20 else df

    header_cols = list(display.columns)
    col_display = {c: c for c in header_cols}

    rows: list[str] = []
    rows.append("<code>")
    rows.append(f"{'날짜':<12} " + " ".join(f"{c:<10}" for c in header_cols))
    rows.append("-" * (12 + 11 * len(header_cols)))
    for idx, row in display.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
        vals = " ".join(f"{_format_number(row.get(c)):>10}" for c in header_cols)
        rows.append(f"{date_str:<12} {vals}")
    rows.append("</code>")

    lines.extend(rows)
    if len(df) > 20:
        lines.append(f"(전체 {len(df)}행 중 최근 20행 표시)")
    return "\n".join(lines)


def main() -> None:
    today = dt.date.today()
    default_start = (today - dt.timedelta(days=30)).strftime("%Y%m%d")
    default_end = today.strftime("%Y%m%d")

    parser = argparse.ArgumentParser(description="지수 기초 지표(배당수익률 포함) 수집 및 텔레그램 전송")
    parser.add_argument("--start-date", default=default_start, help="시작일 (YYYYMMDD 또는 YYYY-MM-DD)")
    parser.add_argument("--end-date", default=default_end, help="종료일 (YYYYMMDD 또는 YYYY-MM-DD)")
    parser.add_argument(
        "--index-codes",
        default="1001,2001",
        help="콤마 구분 지수 코드 (기본: 1001,2001). 예: 1001=KOSPI, 2001=KOSDAQ",
    )
    parser.add_argument("--no-send", action="store_true", help="텔레그램 전송 없이 결과만 출력")
    parser.add_argument("--detail", action="store_true", help="일별 상세 데이터도 전송")
    args = parser.parse_args()

    start_date = _to_iso_date(args.start_date)
    end_date = _to_iso_date(args.end_date)
    index_codes = [c.strip() for c in args.index_codes.split(",") if c.strip()]

    logger.info("수집 시작: %s ~ %s, 지수=%s", start_date, end_date, index_codes)

    provider = CompositeProvider()
    results: dict[str, pd.DataFrame] = {}

    for code in index_codes:
        logger.info("지수 %s 수집 중...", code)
        try:
            df = provider.fetch_index_fundamental(
                start_date=start_date,
                end_date=end_date,
                index_code=code,
            )
            results[code] = df
            logger.info("  → %d행 수집 완료, 컬럼: %s", len(df), list(df.columns))
        except Exception as exc:
            logger.warning("  지수 %s 수집 실패: %s", code, exc)
            results[code] = pd.DataFrame()

    # 콘솔 출력
    summary = _build_text_report(results, start_date, end_date)
    print("\n" + summary.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""))

    if args.no_send:
        logger.info("--no-send 옵션으로 텔레그램 전송 생략")
        return

    sender = TelegramSender()

    # 요약 메시지 전송
    try:
        resp = sender.send_text(summary, parse_mode="HTML")
        if resp.get("ok"):
            logger.info("텔레그램 요약 전송 성공")
        else:
            logger.warning("텔레그램 요약 전송 실패: %s", resp)
    except Exception as exc:
        logger.error("텔레그램 전송 오류: %s", exc)

    # 상세 데이터 전송 (--detail 플래그)
    if args.detail:
        for code, df in results.items():
            if df is not None and not df.empty:
                detail_text = _build_detail_text(code, df)
                try:
                    resp = sender.send_text(detail_text, parse_mode="HTML")
                    if resp.get("ok"):
                        logger.info("텔레그램 상세(%s) 전송 성공", code)
                    else:
                        logger.warning("텔레그램 상세(%s) 전송 실패: %s", code, resp)
                except Exception as exc:
                    logger.error("텔레그램 상세 전송 오류: %s", exc)


if __name__ == "__main__":
    main()
