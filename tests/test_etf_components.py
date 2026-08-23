from __future__ import annotations

import pandas as pd
import pytest

from capybara_fetcher.pipeline.etf_components import (
    COLUMNS,
    EtfComponentCollector,
    KrxCredentialsError,
    build_meta,
    existing_pairs,
    load_etf_universe,
    merge_snapshots,
    normalize_pdf,
    require_krx_credentials,
    weekly_dates,
)


@pytest.fixture(autouse=True)
def _krx_env(monkeypatch):
    monkeypatch.setenv("KRX_ID", "test-id")
    monkeypatch.setenv("KRX_PW", "test-pw")


def _pdf(tickers=("005930", "000660")) -> pd.DataFrame:
    """pykrx가 돌려주는 모양 — index가 티커, 컬럼이 한글."""
    return pd.DataFrame(
        {
            "구성종목명": ["삼성전자", "SK하이닉스"][: len(tickers)],
            "계약수": [8140.0, 968.0][: len(tickers)],
            "금액": [667480000, 118580000][: len(tickers)],
            "시가총액": [667480000, 118580000][: len(tickers)],
            "비중": [31.77, 5.69][: len(tickers)],
        },
        index=list(tickers),
    )


# ── 자격증명 게이트 ────────────────────────────────────────────

def test_missing_credentials_raise(monkeypatch):
    """없으면 빈 결과가 조용히 쌓이므로 시작 전에 멈춰야 한다."""
    monkeypatch.delenv("KRX_ID", raising=False)
    with pytest.raises(KrxCredentialsError, match="KRX_ID"):
        require_krx_credentials()


# ── 정규화 ────────────────────────────────────────────────────

def test_normalize_maps_columns_and_keys():
    out = normalize_pdf(_pdf(), etf_ticker="069500", base_date="20260821")
    assert list(out.columns) == COLUMNS
    assert set(out["COMPONENT_TICKER"]) == {"005930", "000660"}
    assert (out["ETF_TICKER"] == "069500").all()
    assert (out["BASE_DATE"] == pd.Timestamp("2026-08-21")).all()
    assert out.loc[out.COMPONENT_TICKER == "005930", "WEIGHT_PCT"].iloc[0] == pytest.approx(31.77)


def test_normalize_zero_pads_short_tickers():
    """일부 소스가 앞의 0을 떼고 준다. 6자리로 맞춰야 다른 데이터와 조인된다."""
    out = normalize_pdf(_pdf(tickers=("5930",)), etf_ticker="069500", base_date="20260821")
    assert out["COMPONENT_TICKER"].iloc[0] == "005930"


def test_normalize_empty_gives_typed_empty_frame():
    """상장 전이면 컬럼조차 없는 빈 DataFrame이 온다 — 컬럼 존재를 가정하면 안 된다."""
    out = normalize_pdf(pd.DataFrame(), etf_ticker="069500", base_date="20260821")
    assert out.empty and list(out.columns) == COLUMNS


def test_normalize_drops_rows_without_weight():
    """현금·기타 항목이 비중 없이 섞여 온다."""
    raw = _pdf()
    raw.loc["CASH"] = ["원화예금", 0.0, 100, 100, None]
    out = normalize_pdf(raw, etf_ticker="069500", base_date="20260821")
    assert "CASH00" not in set(out["COMPONENT_TICKER"])
    assert len(out) == 2


# ── 주간 격자 ─────────────────────────────────────────────────

def test_weekly_dates_are_fridays_by_default():
    days = weekly_dates("2026-08-01", "2026-08-31")
    assert days == ["20260807", "20260814", "20260821", "20260828"]
    assert all(pd.Timestamp(d).weekday() == 4 for d in days)


def test_weekly_dates_respect_weekday_argument():
    days = weekly_dates("2026-08-01", "2026-08-31", weekday=0)
    assert all(pd.Timestamp(d).weekday() == 0 for d in days)


# ── 증분 ──────────────────────────────────────────────────────

def test_existing_pairs_round_trips():
    frame = normalize_pdf(_pdf(), etf_ticker="069500", base_date="20260821")
    assert existing_pairs(frame) == {("069500", "20260821")}
    assert existing_pairs(None) == set()


def test_collector_skips_already_collected():
    calls: list[tuple[str, str]] = []

    class Provider:
        def fetch_etf_pdf(self, *, ticker, date):
            calls.append((ticker, date))
            return _pdf()

    collector = EtfComponentCollector(provider=Provider(), sleep_sec=0)
    collector.collect(
        tickers=["069500", "091160"],
        dates=["20260814", "20260821"],
        already={("069500", "20260814")},
    )
    assert ("069500", "20260814") not in calls
    assert len(calls) == 3


def test_collector_stops_at_max_calls_and_keeps_what_it_got():
    class Provider:
        def fetch_etf_pdf(self, *, ticker, date):
            return _pdf()

    collector = EtfComponentCollector(provider=Provider(), sleep_sec=0)
    out = collector.collect(
        tickers=["A", "B", "C"], dates=["20260814", "20260821"], max_calls=2
    )
    assert collector.stats.stopped_early is True
    assert collector.stats.requested == 2
    assert len(out) == 4  # 2건 × 구성종목 2개 — 모은 것은 버리지 않는다


def test_collector_counts_empty_and_failed_separately():
    """빈 결과(상장 전)와 실패(조회 오류)는 다르게 세야 원인 판단이 된다."""
    class Provider:
        def __init__(self):
            self.n = 0

        def fetch_etf_pdf(self, *, ticker, date):
            self.n += 1
            if self.n == 1:
                return pd.DataFrame()      # 상장 전
            raise RuntimeError("boom")     # 조회 실패

    collector = EtfComponentCollector(provider=Provider(), sleep_sec=0, max_retries=0)
    collector.collect(tickers=["A", "B"], dates=["20260821"])
    assert collector.stats.empty == 1
    assert collector.stats.failed == 1


# ── 병합 ──────────────────────────────────────────────────────

def test_merge_prefers_fresh_on_duplicate_keys():
    old = normalize_pdf(_pdf(), etf_ticker="069500", base_date="20260821")
    new = old.copy()
    new["WEIGHT_PCT"] = 99.0
    merged = merge_snapshots(old, new)
    assert len(merged) == 2
    assert (merged["WEIGHT_PCT"] == 99.0).all()


def test_merge_handles_missing_previous():
    fresh = normalize_pdf(_pdf(), etf_ticker="069500", base_date="20260821")
    assert len(merge_snapshots(None, fresh)) == 2
    assert merge_snapshots(None, pd.DataFrame(columns=COLUMNS)).empty


# ── 목록 파일 / 메타 ───────────────────────────────────────────

def test_load_universe_skips_comments(tmp_path):
    path = tmp_path / "u.txt"
    path.write_text("# 주석\n\n069500\tKODEX 200\n091160\tKODEX 반도체\n", encoding="utf-8")
    assert load_etf_universe(str(path)) == {"069500": "KODEX 200", "091160": "KODEX 반도체"}


def test_build_meta_summarizes_coverage():
    a = normalize_pdf(_pdf(), etf_ticker="069500", base_date="20260814")
    b = normalize_pdf(_pdf(), etf_ticker="069500", base_date="20260821")
    frame = merge_snapshots(a, b)
    meta = build_meta(frame, stats=EtfComponentCollector(provider=object(), sleep_sec=0).stats,
                      universe_size=38)
    assert meta["rows"] == 4
    assert meta["etf_count"] == 1
    assert meta["date_range"] == {"start": "2026-08-14", "end": "2026-08-21", "snapshots": 2}
    assert meta["snapshots_per_etf"]["069500"] == 2
    assert meta["cadence"] == "weekly"
