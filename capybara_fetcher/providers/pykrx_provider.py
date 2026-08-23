from __future__ import annotations

import datetime as dt
import importlib
from dataclasses import dataclass

import pandas as pd


_PYKRX_STOCK_MODULE = None
_PYKRX_STOCK_IMPORT_ERROR: Exception | None = None


def _get_stock_module():
    global _PYKRX_STOCK_MODULE, _PYKRX_STOCK_IMPORT_ERROR

    if _PYKRX_STOCK_MODULE is not None:
        return _PYKRX_STOCK_MODULE

    if _PYKRX_STOCK_IMPORT_ERROR is not None:
        raise RuntimeError(f"pykrx stock module unavailable: {_PYKRX_STOCK_IMPORT_ERROR}") from _PYKRX_STOCK_IMPORT_ERROR

    try:
        _PYKRX_STOCK_MODULE = importlib.import_module("pykrx.stock")
        return _PYKRX_STOCK_MODULE
    except Exception as exc:
        _PYKRX_STOCK_IMPORT_ERROR = exc
        raise RuntimeError(f"pykrx stock module unavailable: {exc}") from exc


@dataclass(frozen=True)
class PykrxProvider:
    name: str = "pykrx"

    def fetch_ohlcv(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        start = start_date.replace("-", "")
        end = end_date.replace("-", "")
        stock = _get_stock_module()
        return stock.get_market_ohlcv_by_date(start, end, ticker, adjusted=adjusted)

    def fetch_etf_pdf(self, *, ticker: str, date: str) -> pd.DataFrame:
        """ETF 구성종목(PDF, Portfolio Deposit File)을 특정 일자로 조회한다.

        KRX 정보데이터시스템은 이 엔드포인트에 **로그인을 요구한다.** 비로그인으로
        부르면 HTTP 400 `LOGOUT`이 오고 pykrx는 빈 DataFrame을 돌려준다. pykrx가
        `KRX_ID`/`KRX_PW` 환경변수를 읽어 자동 로그인하므로 호출부는 신경 쓸 게 없지만,
        **자격증명이 없으면 조용히 빈 결과가 된다** — 상장 전과 구분되지 않는다.
        그래서 수집 파이프라인이 시작 전에 자격증명 유무를 먼저 확인한다.

        상장 전 일자는 정상적으로 빈 DataFrame이다 (pykrx가 내부 오류를 찍지만
        예외를 던지지는 않는다).

        Returns:
            index=티커, columns=[구성종목명, 계약수, 금액, 시가총액, 비중].
            조회 실패나 상장 전이면 빈 DataFrame.
        """
        stock = _get_stock_module()
        return stock.get_etf_portfolio_deposit_file(ticker, date.replace("-", ""))

    def list_etf_tickers(self, *, date: str) -> list[str]:
        """해당 일자에 상장돼 있던 ETF 티커 전체."""
        stock = _get_stock_module()
        return list(stock.get_etf_ticker_list(date.replace("-", "")))

    def fetch_market_cap(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        start = start_date.replace("-", "")
        end = end_date.replace("-", "")
        stock = _get_stock_module()
        return stock.get_market_cap_by_date(start, end, ticker)

    def load_stock_master(self, *, asof_date: dt.date | None = None) -> pd.DataFrame:
        _ = asof_date
        raise NotImplementedError("PykrxProvider does not provide stock master")

    def list_tickers(
        self,
        *,
        asof_date: dt.date | None = None,
        market: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        _ = asof_date, market
        raise NotImplementedError("PykrxProvider does not provide ticker list in this architecture")
