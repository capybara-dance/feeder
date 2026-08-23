"""ETF 구성종목(PDF) 수집 — 주 단위 스냅샷을 쌓아 시점별 이력을 만든다.

## 왜 필요한가

한국투자증권 API는 ETF 구성종목을 **오늘 스냅샷만** 준다. 과거를 물어볼 파라미터가 없다.
그래서 "그 시점에 그 ETF가 무엇을 담고 있었나"를 알 수 없고, 오늘 구성을 과거에
적용하면 **오늘 편입된 승자 종목이 과거 후보에 섞이는 미래참조**가 생긴다.

KRX 정보데이터시스템의 PDF(Portfolio Deposit File)는 일자를 받는다. 최소 2013년까지
확인했다. 다만 **로그인이 필요하다** — `KRX_ID`/`KRX_PW` 환경변수가 있으면 pykrx가
알아서 로그인한다.

## 왜 주 단위인가

구성종목 *목록*은 거의 안 변한다. 1년치를 주간으로 재보니 변경이 있었던 주가 52주 중
2~4주뿐이었다. 샘플 주기를 늘렸을 때 실제 목록과 어긋나는 종목 수:

    주기      평균 오차종목   최대    (KODEX 200 / KODEX 반도체 / TIGER 200IT)
    2주       0.08 0.49 0.00   2 25 0
    월        0.57 0.53 0.04   8 25 1
    분기      5.23 4.96 0.75  19 25 3

월 단위도 평균은 작지만 **최대가 크다.** KODEX 반도체는 2025-09-12에 지수 개편으로
56→37종목(편출 22)이 한 번에 바뀌었는데, 월 샘플이면 그 오차를 최대 4주 안고 간다.
주 단위면 최대 1주다. 비용 차이가 1시간뿐이라 주 단위를 쓴다.

## 증분

이미 모은 (ETF, 일자)는 다시 받지 않는다. 최초 백필만 오래 걸리고(38종목 × 4.6년 ≈
1.3시간) 이후 주간 실행은 38회 호출 = 30초다.
"""

from __future__ import annotations

import datetime as dt
import os
import time
from dataclasses import dataclass, field

import pandas as pd

# 산출 스키마. `docs/data_dictionary.md`의 ETF_COMPONENT 테이블과 맞춰 두었다 —
# 나중에 Oracle에 적재할 때 컬럼을 다시 만들지 않아도 되게.
COLUMNS = [
    "BASE_DATE",
    "ETF_TICKER",
    "COMPONENT_TICKER",
    "COMPONENT_NAME",
    "SHARES_HELD",
    "AMOUNT",
    "MARKET_CAP",
    "WEIGHT_PCT",
]

# pykrx가 돌려주는 한글 컬럼 → 우리 스키마
_PDF_COLUMN_MAP = {
    "구성종목명": "COMPONENT_NAME",
    "계약수": "SHARES_HELD",
    "금액": "AMOUNT",
    "시가총액": "MARKET_CAP",
    "비중": "WEIGHT_PCT",
}


class KrxCredentialsError(RuntimeError):
    """`KRX_ID`/`KRX_PW`가 없다.

    이게 없으면 KRX가 HTTP 400 `LOGOUT`을 주고 pykrx는 **빈 DataFrame**을 돌려준다.
    상장 전과 구분되지 않아 조용히 "구성종목 0건"으로 쌓이므로, 수집을 시작하기
    전에 여기서 멈춘다.
    """


@dataclass
class CollectionStats:
    requested: int = 0
    fetched: int = 0
    empty: int = 0
    failed: int = 0
    rows: int = 0
    elapsed_sec: float = 0.0
    stopped_early: bool = False

    def as_dict(self) -> dict:
        return {
            "requested": self.requested,
            "fetched": self.fetched,
            "empty": self.empty,
            "failed": self.failed,
            "rows": self.rows,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "stopped_early": self.stopped_early,
        }


@dataclass
class EtfComponentCollector:
    """주 단위 PDF 수집기.

    `provider`는 `CompositeProvider`여야 한다 (`AGENTS.md`의 provider 캡슐화 규칙).
    """

    provider: object
    sleep_sec: float = 0.2
    max_retries: int = 2
    stats: CollectionStats = field(default_factory=CollectionStats)

    def __post_init__(self) -> None:
        require_krx_credentials()

    def collect(
        self,
        *,
        tickers: list[str],
        dates: list[str],
        already: set[tuple[str, str]] | None = None,
        max_calls: int | None = None,
    ) -> pd.DataFrame:
        """(ETF, 일자) 격자를 훑어 구성종목을 모은다.

        `already`에 있는 조합은 건너뛴다(증분). `max_calls`에 닿으면 거기서 멈추고
        `stats.stopped_early`를 세운다 — GitHub Actions 6시간 제한 방어용이다.
        모은 것까지는 그대로 돌려주므로 다음 실행이 이어받는다.
        """
        already = already or set()
        started = time.perf_counter()
        frames: list[pd.DataFrame] = []
        calls = 0

        # 오래된 날짜부터 채운다 — 중간에 끊겨도 "어디까지 채웠나"가 연속 구간이 된다
        for date in sorted(dates):
            for ticker in tickers:
                if (ticker, date) in already:
                    continue
                if max_calls is not None and calls >= max_calls:
                    self.stats.stopped_early = True
                    self.stats.elapsed_sec = time.perf_counter() - started
                    return _concat(frames)

                self.stats.requested += 1
                calls += 1
                frame = self._fetch_one(ticker=ticker, date=date)
                if frame is None:
                    self.stats.failed += 1
                elif frame.empty:
                    # 상장 전이거나 그날 PDF가 공시되지 않았다. 정상이다.
                    self.stats.empty += 1
                else:
                    self.stats.fetched += 1
                    self.stats.rows += len(frame)
                    frames.append(frame)
                if self.sleep_sec:
                    time.sleep(self.sleep_sec)

        self.stats.elapsed_sec = time.perf_counter() - started
        return _concat(frames)

    def _fetch_one(self, *, ticker: str, date: str) -> pd.DataFrame | None:
        """한 건 조회. 실패하면 재시도하고, 끝내 실패하면 None(빈 결과와 구분)."""
        for attempt in range(self.max_retries + 1):
            try:
                raw = self.provider.fetch_etf_pdf(ticker=ticker, date=date)
            except Exception:
                raw = None
            if raw is not None:
                return normalize_pdf(raw, etf_ticker=ticker, base_date=date)
            if attempt < self.max_retries:
                time.sleep(1.0 * (attempt + 1))
        return None


def normalize_pdf(raw: pd.DataFrame, *, etf_ticker: str, base_date: str) -> pd.DataFrame:
    """pykrx의 PDF 응답을 우리 스키마로 바꾼다.

    응답은 index가 구성종목 티커이고 컬럼이 한글이다. 상장 전이면 빈 DataFrame이
    오는데, 그때는 컬럼조차 없으므로 컬럼 존재를 가정하면 안 된다.
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=COLUMNS)

    out = raw.rename(columns=_PDF_COLUMN_MAP).copy()
    out["COMPONENT_TICKER"] = [str(v).zfill(6) for v in out.index]
    out["ETF_TICKER"] = etf_ticker
    out["BASE_DATE"] = pd.Timestamp(base_date).normalize()

    for column in COLUMNS:
        if column not in out.columns:
            out[column] = pd.NA
    out = out[COLUMNS].reset_index(drop=True)

    # 구성종목 티커가 비었거나 비중이 없는 행은 버린다 — 현금·기타 항목이 섞여 온다
    out = out[out["COMPONENT_TICKER"].str.strip().ne("") & out["WEIGHT_PCT"].notna()]
    return out.reset_index(drop=True)


def weekly_dates(start: str, end: str, *, weekday: int = 4) -> list[str]:
    """`start`~`end` 사이의 주 1회 기준일 (기본 금요일).

    공휴일이라 PDF가 없는 날은 빈 결과가 되는데, 그건 그대로 둔다 — 그 주를 통째로
    비우는 것보다 낫고, 다음 주 스냅샷이 이어받는다. 요일을 바꾸고 싶으면
    `weekday`(월=0 … 금=4)를 준다.
    """
    days = pd.bdate_range(start, end, freq=f"W-{['MON','TUE','WED','THU','FRI'][weekday]}")
    return [d.strftime("%Y%m%d") for d in days]


def load_etf_universe(path: str) -> dict[str, str]:
    """`코드<TAB>이름` 형식의 대상 ETF 목록을 읽는다 (`#`은 주석)."""
    universe: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            code = parts[0].strip()
            if code:
                universe[code] = parts[1].strip() if len(parts) > 1 else code
    return universe


def require_krx_credentials() -> None:
    missing = [name for name in ("KRX_ID", "KRX_PW") if not os.getenv(name, "").strip()]
    if missing:
        raise KrxCredentialsError(
            "KRX 로그인 정보가 없습니다: "
            + ", ".join(missing)
            + " — 없으면 KRX가 HTTP 400 LOGOUT을 주고 pykrx가 빈 결과를 돌려주는데,"
            " 그건 '상장 전'과 구분되지 않아 빈 데이터가 조용히 쌓입니다."
        )


def existing_pairs(frame: pd.DataFrame | None) -> set[tuple[str, str]]:
    """이미 모은 (ETF, 일자) 조합. 증분 수집의 기준."""
    if frame is None or frame.empty:
        return set()
    dates = pd.to_datetime(frame["BASE_DATE"]).dt.strftime("%Y%m%d")
    return set(zip(frame["ETF_TICKER"].astype(str), dates))


def merge_snapshots(previous: pd.DataFrame | None, fresh: pd.DataFrame) -> pd.DataFrame:
    """기존 이력에 새로 모은 것을 붙인다. 같은 (ETF, 일자, 종목)은 새 값이 이긴다."""
    parts = [f for f in (previous, fresh) if f is not None and not f.empty]
    if not parts:
        return pd.DataFrame(columns=COLUMNS)
    merged = pd.concat(parts, ignore_index=True)
    merged["BASE_DATE"] = pd.to_datetime(merged["BASE_DATE"]).dt.normalize()
    merged = merged.drop_duplicates(
        subset=["ETF_TICKER", "BASE_DATE", "COMPONENT_TICKER"], keep="last"
    )
    return merged.sort_values(["BASE_DATE", "ETF_TICKER", "WEIGHT_PCT"],
                              ascending=[True, True, False]).reset_index(drop=True)


def build_meta(frame: pd.DataFrame, *, stats: CollectionStats, universe_size: int) -> dict:
    """릴리즈에 함께 올릴 메타. 363MB를 받기 전에 이것만 보고 판단할 수 있게 한다."""
    if frame.empty:
        coverage: dict = {}
        date_range = {"start": None, "end": None, "snapshots": 0}
    else:
        dates = pd.to_datetime(frame["BASE_DATE"])
        date_range = {
            "start": dates.min().strftime("%Y-%m-%d"),
            "end": dates.max().strftime("%Y-%m-%d"),
            "snapshots": int(dates.nunique()),
        }
        coverage = (
            frame.groupby("ETF_TICKER")["BASE_DATE"].nunique().sort_index().astype(int).to_dict()
        )
    return {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "rows": int(len(frame)),
        "etf_count": int(frame["ETF_TICKER"].nunique()) if not frame.empty else 0,
        "universe_size": universe_size,
        "date_range": date_range,
        "snapshots_per_etf": coverage,
        "collection": stats.as_dict(),
        "source": "KRX 정보데이터시스템 PDF (pykrx get_etf_portfolio_deposit_file)",
        "cadence": "weekly",
    }


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(frames, ignore_index=True)
