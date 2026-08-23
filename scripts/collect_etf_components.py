"""ETF 구성종목(PDF) 주간 스냅샷을 모아 parquet으로 떨군다.

    python scripts/collect_etf_components.py                      # 증분 (최신 릴리즈에 이어붙임)
    python scripts/collect_etf_components.py --start-date 2022-01-01   # 백필 범위 지정
    python scripts/collect_etf_components.py --max-calls 15000    # 이번 실행 상한 (Actions 방어)
    python scripts/collect_etf_components.py --no-resume          # 릴리즈 무시하고 처음부터
    python scripts/collect_etf_components.py --all                # 대상 목록 대신 상장 ETF 전체

산출:
    cache/etf_components.parquet
    cache/etf_components.meta.json

⚠️ `KRX_ID`/`KRX_PW`가 없으면 KRX가 HTTP 400 LOGOUT을 주고 pykrx가 빈 결과를 돌려준다.
그건 '상장 전'과 구분되지 않아 빈 데이터가 조용히 쌓이므로 **시작 전에 멈춘다.**
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.dotenv_loader import load_dotenv_if_present  # noqa: E402

# pykrx는 import 시점에 KRX_ID/KRX_PW를 읽으므로 .env를 먼저 불러야 한다
# (`scripts/sync_oracle.py`가 같은 이유로 같은 순서를 지킨다)
load_dotenv_if_present(REPO_ROOT / ".env")

import pandas as pd  # noqa: E402

from capybara_fetcher.pipeline.etf_components import (  # noqa: E402
    COLUMNS,
    EtfComponentCollector,
    build_meta,
    existing_pairs,
    load_etf_universe,
    merge_snapshots,
    require_krx_credentials,
    weekly_dates,
)
from capybara_fetcher.pipeline.release_ingest import (  # noqa: E402
    _read_parquet_url,
    _resolve_release,
)
from capybara_fetcher.providers.composite_provider import CompositeProvider  # noqa: E402

DEFAULT_UNIVERSE = REPO_ROOT / "config" / "etf_universe.txt"
DEFAULT_OUT = REPO_ROOT / "cache" / "etf_components.parquet"
ASSET_NAME = "etf_components.parquet"


def _load_previous(repo: str, tag: str | None, token: str | None) -> tuple[pd.DataFrame | None, str | None]:
    """직전 릴리즈의 parquet을 받아 온다. 없으면 (None, None)."""
    try:
        info, assets = _resolve_release(repo, tag=tag, token=token)
    except Exception as exc:
        print(f"[resume] 직전 릴리즈를 찾지 못했습니다 ({exc}) — 처음부터 모읍니다.")
        return None, None
    url = assets.get(ASSET_NAME)
    if not url:
        print(f"[resume] 릴리즈 {info.tag}에 {ASSET_NAME}이 없습니다 — 처음부터 모읍니다.")
        return None, None
    print(f"[resume] 릴리즈 {info.tag}에서 기존 이력을 받는 중…")
    frame = _read_parquet_url(url, token=token)
    print(f"[resume] {len(frame):,}행 / {frame['BASE_DATE'].nunique()}개 시점")
    return frame, info.tag


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ETF 구성종목(PDF) 주간 수집")
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--all", action="store_true", help="대상 목록 대신 상장 ETF 전체")
    parser.add_argument("--start-date", default="2022-01-01")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--weekday", type=int, default=4, help="주간 기준 요일 (월=0 … 금=4)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sleep", type=float, default=0.2, help="호출 간 대기(초)")
    parser.add_argument("--max-calls", type=int, default=0, help="이번 실행 호출 상한 (0=무제한)")
    parser.add_argument("--no-resume", action="store_true", help="직전 릴리즈를 이어받지 않는다")
    parser.add_argument("--release-repo", default="capybara-dance/feeder")
    parser.add_argument("--release-tag", default="", help="이어받을 릴리즈 태그 (빈 값=latest)")
    args = parser.parse_args(argv)

    # 자격증명이 없으면 여기서 멈춘다 — 빈 데이터가 쌓이는 것보다 실패가 낫다
    require_krx_credentials()

    end_date = args.end_date or pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d")
    dates = weekly_dates(args.start_date, end_date, weekday=args.weekday)

    provider = CompositeProvider()
    if args.all:
        tickers = provider.list_etf_tickers(date=dates[-1])
        print(f"대상: 상장 ETF 전체 {len(tickers)}개")
    else:
        universe = load_etf_universe(str(args.universe))
        tickers = sorted(universe)
        print(f"대상: {args.universe.name} {len(tickers)}개")

    token = os.getenv("GITHUB_TOKEN") or None
    previous, prev_tag = (None, None)
    if not args.no_resume:
        previous, prev_tag = _load_previous(
            args.release_repo, args.release_tag or None, token
        )
    already = existing_pairs(previous)

    todo = sum(1 for d in dates for t in tickers if (t, d) not in already)
    print(f"기간 {args.start_date} ~ {end_date} / 주간 시점 {len(dates)}개")
    print(f"이미 모은 조합 {len(already):,} / 이번에 받을 조합 {todo:,}")
    if args.max_calls:
        print(f"이번 실행 상한 {args.max_calls:,}회")
    if todo == 0:
        print("새로 받을 게 없습니다.")

    collector = EtfComponentCollector(provider=provider, sleep_sec=args.sleep)
    fresh = collector.collect(
        tickers=tickers,
        dates=dates,
        already=already,
        max_calls=args.max_calls or None,
    )
    stats = collector.stats
    print(
        f"조회 {stats.requested:,}건 → 성공 {stats.fetched:,} / 빈 결과 {stats.empty:,}"
        f" / 실패 {stats.failed:,} / {stats.rows:,}행 / {stats.elapsed_sec/60:.1f}분"
    )
    if stats.stopped_early:
        print("⚠️ 호출 상한에 걸려 중단했습니다 — 다음 실행이 이어받습니다.")

    merged = merge_snapshots(previous, fresh)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    merged[COLUMNS].to_parquet(args.out, index=False)

    meta = build_meta(merged, stats=stats, universe_size=len(tickers))
    meta["resumed_from"] = prev_tag
    meta_path = args.out.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    rng = meta["date_range"]
    print(f"저장: {args.out} ({len(merged):,}행)")
    print(f"      {rng['start']} ~ {rng['end']} / {rng['snapshots']}개 시점 / ETF {meta['etf_count']}개")

    # 실패가 절반을 넘으면 뭔가 잘못된 것이다 (로그인 만료·차단 등) — 종료코드로 알린다
    if stats.requested and stats.failed / stats.requested > 0.5:
        print("⚠️ 실패율이 50%를 넘습니다 — KRX 세션이나 차단 여부를 확인하세요.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
