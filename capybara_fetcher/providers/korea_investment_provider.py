from __future__ import annotations

import warnings
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class KoreaInvestmentProvider:
    """Lightweight KIS provider for market-cap snapshot fallback.

    Uses publicly downloadable KIS master files (no auth) to extract
    ticker-level market-cap snapshot values.
    """

    name: str = "korea_investment"

    @staticmethod
    @lru_cache(maxsize=1)
    def _snapshot_map_cached() -> dict[str, float]:
        provider = KoreaInvestmentProvider()
        kospi = provider._get_kospi_constituents()
        kosdaq = provider._get_kosdaq_constituents()
        frames = [df for df in [kospi, kosdaq] if not df.empty and "단축코드" in df.columns]
        if not frames:
            return {}

        df = pd.concat(frames, ignore_index=True)
        if "단축코드" not in df.columns:
            return {}

        base_price_col = "기준가" if "기준가" in df.columns else None
        listed_shares_col = "상장주수" if "상장주수" in df.columns else None
        raw_cap_col = None
        if "시가총액" in df.columns:
            raw_cap_col = "시가총액"
        else:
            for c in df.columns:
                if "시가총액" in str(c):
                    raw_cap_col = c
                    break

        out = df[["단축코드"]].copy()
        if base_price_col and listed_shares_col:
            base_price = pd.to_numeric(df[base_price_col], errors="coerce")
            listed_shares = pd.to_numeric(df[listed_shares_col], errors="coerce")
            recalculated_cap = base_price * listed_shares * 1000
            out["MARKET_CAP"] = recalculated_cap.where(recalculated_cap > 0)
        else:
            out["MARKET_CAP"] = pd.Series([pd.NA] * len(out), index=out.index)

        if raw_cap_col is not None:
            raw_cap = pd.to_numeric(df[raw_cap_col], errors="coerce")
            out["MARKET_CAP"] = out["MARKET_CAP"].combine_first(raw_cap)

        out["단축코드"] = out["단축코드"].astype(str).str.strip().str.zfill(6)
        out["MARKET_CAP"] = pd.to_numeric(out["MARKET_CAP"], errors="coerce")
        out = out.dropna(subset=["단축코드", "MARKET_CAP"]).drop_duplicates(subset=["단축코드"], keep="first")
        return dict(zip(out["단축코드"], out["MARKET_CAP"].astype(float)))

    def _parse_master(self, *, url: str, zip_name: str, mst_name: str, suffix_len: int, widths: list[int], columns: list[str]) -> pd.DataFrame:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / zip_name
            mst_path = Path(tmp) / mst_name
            part1_path = Path(tmp) / f"{mst_name}_part1.tmp"
            part2_path = Path(tmp) / f"{mst_name}_part2.tmp"

            try:
                urllib.request.urlretrieve(url, zip_path)
            except Exception as e:
                warnings.warn(f"Failed to download KIS master ({mst_name}): {e}")
                return pd.DataFrame()

            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(tmp)
            except Exception as e:
                warnings.warn(f"Failed to extract KIS master ({mst_name}): {e}")
                return pd.DataFrame()

            try:
                with open(part1_path, "w", encoding="utf-8") as w1, open(part2_path, "w", encoding="utf-8") as w2:
                    with open(mst_path, "r", encoding="cp949") as rf:
                        for row in rf:
                            left = row[0 : len(row) - suffix_len]
                            code = left[0:9].rstrip()
                            std_code = left[9:21].rstrip()
                            name = left[21:].strip()
                            w1.write(f"{code},{std_code},{name}\n")
                            w2.write(row[-suffix_len:])

                df1 = pd.read_csv(part1_path, header=None, names=["단축코드", "표준코드", "한글명"], encoding="utf-8")
                names = list(columns)
                if len(names) < len(widths):
                    names = names + [f"COL_{i}" for i in range(len(names), len(widths))]
                elif len(names) > len(widths):
                    names = names[: len(widths)]

                df2 = pd.read_fwf(part2_path, widths=widths, names=names)
                return pd.merge(df1, df2, how="outer", left_index=True, right_index=True)
            except Exception as e:
                warnings.warn(f"Failed to parse KIS master ({mst_name}): {e}")
                return pd.DataFrame()

    def _get_kospi_constituents(self) -> pd.DataFrame:
        widths = [2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1]
        columns = ['그룹코드', '시가총액규모', '지수업종대분류', '지수업종중분류', '지수업종소분류',
                   '제조업', '저유동성', '지배구조지수종목', 'KOSPI200섹터업종', 'KOSPI100',
                   'KOSPI50', 'KRX', 'ETP', 'ELW발행', 'KRX100',
                   'KRX자동차', 'KRX반도체', 'KRX바이오', 'KRX은행', 'SPAC',
                   'KRX에너지화학', 'KRX철강', '단기과열', 'KRX미디어통신', 'KRX건설',
                   'Non1', 'KRX증권', 'KRX선박', 'KRX섹터_보험', 'KRX섹터_운송',
                   'SRI', '기준가', '매매수량단위', '시간외수량단위', '거래정지',
                   '정리매매', '관리종목', '시장경고', '경고예고', '불성실공시',
                   '우회상장', '락구분', '액면변경', '증자구분', '증거금비율',
                   '신용가능', '신용기간', '전일거래량', '액면가', '상장일자',
                   '상장주수', '자본금', '결산월', '공모가', '우선주',
                   '공매도과열', '이상급등', 'KRX300', 'KOSPI', '매출액',
                   '영업이익', '경상이익', '당기순이익', 'ROE', '기준년월',
                   '시가총액', '그룹사코드', '회사신용한도초과', '담보대출가능', '대주가능']
        return self._parse_master(
            url="https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
            zip_name="kospi_code.mst.zip",
            mst_name="kospi_code.mst",
            suffix_len=228,
            widths=widths,
            columns=columns,
        )

    def _get_kosdaq_constituents(self) -> pd.DataFrame:
        widths = [2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1]
        columns = ['증권그룹구분코드', '시가총액규모구분코드',
                   '지수업종대분류코드', '지수업종중분류코드', '지수업종소분류코드', '벤처기업여부', '저유동성종목여부',
                   'KRX종목여부', 'ETP상품구분코드', 'KRX100종목여부',
                   'KRX자동차여부', 'KRX반도체여부', 'KRX바이오여부', 'KRX은행여부', '기업인수목적회사여부',
                   'KRX에너지화학여부', 'KRX철강여부', '단기과열종목구분코드', 'KRX미디어통신여부', 'KRX건설여부',
                   '투자주의환기종목여부', 'KRX증권구분', 'KRX선박구분', 'KRX섹터보험여부', 'KRX섹터운송여부',
                   'KOSDAQ150지수여부', '주식기준가', '정규시장매매수량단위', '시간외시장매매수량단위', '거래정지여부',
                   '정리매매여부', '관리종목여부', '시장경고구분코드', '시장경고위험예고여부', '불성실공시여부',
                   '우회상장여부', '락구분코드', '액면가변경구분코드', '증자구분코드', '증거금비율',
                   '신용주문가능여부', '신용기간', '전일거래량', '주식액면가', '주식상장일자',
                   '상장주수', '자본금', '결산월', '공모가격', '우선주구분코드',
                   '공매도과열종목여부', '이상급등종목여부', 'KRX300종목여부', '매출액', '영업이익',
                   '경상이익', '당기순이익', 'ROE', '기준년월', '시가총액',
                   '그룹사코드', '회사신용한도초과여부', '담보대출가능여부', '대주가능여부']
        return self._parse_master(
            url="https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
            zip_name="kosdaq_code.mst.zip",
            mst_name="kosdaq_code.mst",
            suffix_len=222,
            widths=widths,
            columns=columns,
        )

    def fetch_market_cap_snapshot(self, ticker: str) -> float | None:
        """Return recalc'd market-cap snapshot from KIS master files.

        Uses base price * listed shares * 1000 when available, and only
        falls back to the raw file field when the derived value cannot be
        computed.
        """
        code = str(ticker).zfill(6)
        snapshot_map = self._snapshot_map_cached()
        return snapshot_map.get(code)
