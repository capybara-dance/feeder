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

이미 모은 (ETF, 일자)는 다시 받지 않는다. **빈 결과는 기록하지 않으므로** 다음 실행이
다시 시도한다 — 중간에 끊겨도 구멍이 굳지 않는다.

## ⚠️ KRX는 대량 요청을 차단한다 (2026-08-23 실측)

첫 백필(9,196건 시도)에서 **약 90건을 받고 막혔다.** 그 뒤 3.7시간 동안 9,099건이
전부 빈 결과였고, 스냅샷은 240개 중 6개만 남았다.

**차단은 예외가 아니라 빈 DataFrame으로 온다.** pykrx가 그렇게 돌려준다. 그래서
"상장 전"과 구분되지 않고, 실패 카운트에도 안 잡혀 실패율 가드가 무력화됐다.

두 가지로 막는다.

1. **탐침(probe)** — 빈 결과가 연달아 나오면 *반드시 데이터가 있는* 조합을 하나
   찔러본다. 그것도 비었으면 차단이다. 휴장일이라 그날 전부 비는 경우와 구분된다.
2. **상장 여부 사전 확인** — 그날 상장돼 있던 ETF만 조회한다. 상장 전 조합을 묻지
   않으므로 빈 결과 자체가 드물어지고, 연속 빈 결과가 차단의 강한 신호가 된다.

차단되면 쿨다운 후 탐침을 다시 던져 회복을 기다린다. 끝내 안 풀리면 **거기서 멈추고
모은 것까지 돌려준다** — 다음 실행이 이어받는다.
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


# 차단 판정용 탐침. 벤치마크 ETF는 전 구간 상장돼 있어 빈 결과가 나올 이유가 없다.
PROBE_TICKER = "069500"


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
    skipped_unlisted: int = 0
    blocks_seen: int = 0
    blocked: bool = False

    def as_dict(self) -> dict:
        return {
            "requested": self.requested,
            "fetched": self.fetched,
            "empty": self.empty,
            "failed": self.failed,
            "rows": self.rows,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "stopped_early": self.stopped_early,
            "skipped_unlisted": self.skipped_unlisted,
            "blocks_seen": self.blocks_seen,
            "blocked": self.blocked,
        }


@dataclass
class EtfComponentCollector:
    """주 단위 PDF 수집기.

    `provider`는 `CompositeProvider`여야 한다 (`AGENTS.md`의 provider 캡슐화 규칙).

    기본 간격이 0.6초인 이유: 0.2초로 9,196건을 시도했다가 약 90건에서 차단당했다
    (2026-08-23). 정확한 임계값은 모르므로 보수적으로 잡고, 그래도 막히면 탐침이
    잡아낸다.
    """

    provider: object
    sleep_sec: float = 0.6
    max_retries: int = 2
    # 빈 결과가 이만큼 연달아 나오면 차단인지 탐침으로 확인한다.
    probe_after_empty: int = 20
    # 차단이 확인되면 이만큼 쉬었다 다시 탐침한다.
    cooldown_sec: float = 300.0
    # 쿨다운을 이 횟수만큼 시도하고도 안 풀리면 멈춘다.
    max_blocks: int = 3
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
        listed_by_date: dict[str, set[str]] | None = None,
    ) -> pd.DataFrame:
        """(ETF, 일자) 격자를 훑어 구성종목을 모은다.

        `already`에 있는 조합은 건너뛴다(증분). `listed_by_date`를 주면 **그날 상장돼
        있던 ETF만** 묻는다 — 상장 전 조합을 빼면 빈 결과가 드물어져 차단 감지가 정확해진다.

        멈추는 경우가 둘이다. 어느 쪽이든 **모은 것까지는 그대로 돌려주므로** 다음
        실행이 이어받는다.

        - `max_calls`에 닿음 → `stats.stopped_early`
        - KRX 차단이 쿨다운으로도 안 풀림 → `stats.blocked`
        """
        already = already or set()
        started = time.perf_counter()
        frames: list[pd.DataFrame] = []
        calls = 0
        consecutive_empty = 0
        probe_date = max(dates) if dates else None

        # 오래된 날짜부터 채운다 — 중간에 끊겨도 "어디까지 채웠나"가 연속 구간이 된다
        for date in sorted(dates):
            listed = listed_by_date.get(date) if listed_by_date else None
            for ticker in tickers:
                if (ticker, date) in already:
                    continue
                if listed is not None and ticker not in listed:
                    # 그날 상장 전이다. 물어봐야 빈 결과이므로 호출을 아낀다.
                    self.stats.skipped_unlisted += 1
                    continue
                if max_calls is not None and calls >= max_calls:
                    self.stats.stopped_early = True
                    return self._finish(frames, started)

                self.stats.requested += 1
                calls += 1
                frame = self._fetch_one(ticker=ticker, date=date)
                if frame is None:
                    self.stats.failed += 1
                    consecutive_empty = 0
                elif frame.empty:
                    self.stats.empty += 1
                    consecutive_empty += 1
                    if consecutive_empty >= self.probe_after_empty:
                        if not self._wait_out_block(probe_date):
                            self.stats.blocked = True
                            return self._finish(frames, started)
                        consecutive_empty = 0
                else:
                    self.stats.fetched += 1
                    self.stats.rows += len(frame)
                    frames.append(frame)
                    consecutive_empty = 0
                if self.sleep_sec:
                    time.sleep(self.sleep_sec)

        return self._finish(frames, started)

    # ── 차단 감지 ────────────────────────────────────────────────

    def _is_alive(self, probe_date: str | None) -> bool:
        """**반드시 데이터가 있는** 조합을 하나 찔러본다. 비어 오면 차단이다.

        휴장일이라 그날 전 종목이 비는 경우와 구분하려고 탐침 날짜를 따로 둔다 —
        수집 범위의 마지막 날짜는 최근 거래일이라 벤치마크 ETF가 반드시 응답한다.
        """
        if probe_date is None:
            return True
        try:
            frame = self.provider.fetch_etf_pdf(ticker=PROBE_TICKER, date=probe_date)
        except Exception:
            return False
        return frame is not None and not frame.empty

    def _wait_out_block(self, probe_date: str | None) -> bool:
        """차단인지 확인하고, 맞으면 쿨다운하며 회복을 기다린다.

        Returns:
            계속 진행해도 되면 True. 끝내 안 풀렸으면 False.
        """
        if self._is_alive(probe_date):
            # 차단이 아니다 — 그 구간이 정말로 비어 있었을 뿐이다(휴장일 등).
            return True

        for attempt in range(1, self.max_blocks + 1):
            self.stats.blocks_seen += 1
            print(
                f"  ⚠️ KRX 차단으로 보입니다 (탐침 실패). "
                f"{self.cooldown_sec / 60:.0f}분 쉬었다 재시도합니다 "
                f"[{attempt}/{self.max_blocks}]"
            )
            time.sleep(self.cooldown_sec)
            if self._is_alive(probe_date):
                print("  ✅ 회복됐습니다. 수집을 이어갑니다.")
                return True
        print("  ⛔ 쿨다운으로도 안 풀립니다. 여기서 멈추고 모은 것까지 저장합니다.")
        return False

    def _finish(self, frames: list[pd.DataFrame], started: float) -> pd.DataFrame:
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


def fetch_listed_by_date(provider: object, dates: list[str], *, sleep_sec: float = 0.4) -> dict[str, set[str]]:
    """일자별로 그날 상장돼 있던 ETF 집합을 받아 온다.

    이걸 알면 상장 전 조합을 아예 묻지 않는다. 호출이 25%쯤 줄어드는 것보다 중요한 건
    **빈 결과가 드물어져 차단 감지가 정확해진다**는 점이다 — 상장 전 빈 결과가 섞이면
    "연속 빈 결과"가 차단의 신호로 쓸모없어진다.

    실패한 날짜는 결과에서 빼둔다. 그 날짜는 필터 없이(=전부 조회) 진행된다 —
    목록을 못 받았다고 그날을 통째로 건너뛰면 데이터가 사라진다.
    """
    listed: dict[str, set[str]] = {}
    for date in sorted(dates):
        try:
            codes = provider.list_etf_tickers(date=date)
        except Exception:
            codes = []
        if codes:
            listed[date] = set(codes)
        if sleep_sec:
            time.sleep(sleep_sec)
    return listed


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
