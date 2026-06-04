"""
Korea Investment Securities API data provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
import os
import ssl
import threading
import tempfile
import urllib.request
import warnings
import zipfile

import pandas as pd

from ..provider import DataProvider
from .korea_investment_auth import KISAuth
from .provider_utils import load_master_json


@dataclass(frozen=True)
class KoreaInvestmentProvider(DataProvider):
    """
    DataProvider implementation using Korea Investment Securities API:
    - tickers/master: local Seibro-derived JSON (same as pykrx)
    - ohlcv: Korea Investment API
    """

    master_json_path: str
    appkey: str
    appsecret: str
    base_url: str = "https://openapi.koreainvestment.com:9443"
    name: str = "korea_investment"
    _auth: KISAuth | None = field(default=None, init=False, repr=False, compare=False)
    _auth_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def _get_auth(self) -> KISAuth:
        """Get KIS authentication instance (cached to reuse token across session)."""
        # Use object.__setattr__ to bypass frozen dataclass restriction
        # Thread-safe lazy initialization with double-checked locking
        auth = object.__getattribute__(self, '_auth')
        if auth is None:
            lock = object.__getattribute__(self, '_auth_lock')
            with lock:
                # Double-check after acquiring lock
                auth = object.__getattribute__(self, '_auth')
                if auth is None:
                    auth = KISAuth(self.appkey, self.appsecret, self.base_url)
                    object.__setattr__(self, '_auth', auth)
        return auth

    def load_stock_master(self, *, asof_date: dt.date | None = None) -> pd.DataFrame:
        """Load stock master from local JSON file."""
        # asof_date reserved for future providers
        return load_master_json(self.master_json_path)

    def list_tickers(
        self,
        *,
        asof_date: dt.date | None = None,
        market: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        """
        List tickers using Korea Investment master files.

        Market mapping rules (based on official KIS master fields):
        - KOSPI: get_kospi_constituents()['그룹코드'] == 'ST'
        - ETF: get_kospi_constituents()['그룹코드'] == 'EF'
        - KOSDAQ: get_kosdaq_constituents()['증권그룹구분코드'] == 'ST'

        Returns same shape as FdrProvider.list_tickers():
        (sorted ticker list, market_by_ticker dict)
        """
        master = self._build_master_from_kis()

        if market:
            m = str(market).strip()
            master = master[master["Market"] == m]

        tickers = master["Code"].astype(str).str.zfill(6).unique().tolist()
        tickers = sorted(tickers)

        ticker_codes = master["Code"].astype(str).str.zfill(6).tolist()
        market_by_ticker = dict(zip(ticker_codes, master["Market"].tolist()))

        return tickers, market_by_ticker

    def _build_master_from_kis(self) -> pd.DataFrame:
        """Build combined market master (KOSPI/KOSDAQ/ETF) from KIS files."""
        df_kospi = self._get_kospi_constituents()
        df_kosdaq = self._get_kosdaq_constituents()

        frames: list[pd.DataFrame] = []

        if not df_kospi.empty:
            kospi_st = df_kospi[df_kospi["그룹코드"].astype(str).str.strip() == "ST"].copy()
            if not kospi_st.empty:
                kospi_st["Market"] = "KOSPI"
                frames.append(kospi_st[["단축코드", "Market"]].rename(columns={"단축코드": "Code"}))

            kospi_ef = df_kospi[df_kospi["그룹코드"].astype(str).str.strip() == "EF"].copy()
            if not kospi_ef.empty:
                kospi_ef["Market"] = "ETF"
                frames.append(kospi_ef[["단축코드", "Market"]].rename(columns={"단축코드": "Code"}))

        if not df_kosdaq.empty:
            kosdaq_st = df_kosdaq[df_kosdaq["증권그룹구분코드"].astype(str).str.strip() == "ST"].copy()
            if not kosdaq_st.empty:
                kosdaq_st["Market"] = "KOSDAQ"
                frames.append(kosdaq_st[["단축코드", "Market"]].rename(columns={"단축코드": "Code"}))

        if not frames:
            return pd.DataFrame(columns=["Code", "Market"])

        out = pd.concat(frames, ignore_index=True)
        out["Code"] = out["Code"].astype(str).str.strip().str.zfill(6)
        out["Market"] = out["Market"].astype(str).str.strip()
        out = out.dropna(subset=["Code"]).drop_duplicates(subset=["Code", "Market"])
        return out

    def _get_kospi_constituents(self) -> pd.DataFrame:
        """Fetch and parse KOSPI master file from KIS."""
        url = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
        zip_name = "kospi_code.mst.zip"
        mst_name = "kospi_code.mst"

        field_specs = [2, 1, 4, 4, 4,
                       1, 1, 1, 1, 1,
                       1, 1, 1, 1, 1,
                       1, 1, 1, 1, 1,
                       1, 1, 1, 1, 1,
                       1, 1, 1, 1, 1,
                       1, 9, 5, 5, 1,
                       1, 1, 2, 1, 1,
                       1, 2, 2, 2, 3,
                       1, 3, 12, 12, 8,
                       15, 21, 2, 7, 1,
                       1, 1, 1, 1, 9,
                       9, 9, 5, 9, 8,
                       9, 3, 1, 1, 1]

        part2_columns = ['그룹코드', '시가총액규모', '지수업종대분류', '지수업종중분류', '지수업종소분류',
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

        return self._parse_kis_master(
            url=url,
            zip_name=zip_name,
            mst_name=mst_name,
            part2_suffix_len=228,
            field_specs=field_specs,
            part2_columns=part2_columns,
        )

    def _get_kosdaq_constituents(self) -> pd.DataFrame:
        """Fetch and parse KOSDAQ master file from KIS."""
        url = "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip"
        zip_name = "kosdaq_code.mst.zip"
        mst_name = "kosdaq_code.mst"

        field_specs = [2, 1,
                       4, 4, 4, 1, 1,
                       1, 1, 1, 1, 1,
                       1, 1, 1, 1, 1,
                       1, 1, 1, 1, 1,
                       1, 1, 1, 1, 9,
                       5, 5, 1, 1, 1,
                       2, 1, 1, 1, 2,
                       2, 2, 3, 1, 3,
                       12, 12, 8, 15, 21,
                       2, 7, 1, 1, 1,
                       1, 9, 9, 9, 5,
                       9, 8, 9, 3, 1,
                       1, 1]

        part2_columns = ['증권그룹구분코드', '시가총액규모구분코드',
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

        return self._parse_kis_master(
            url=url,
            zip_name=zip_name,
            mst_name=mst_name,
            part2_suffix_len=222,
            field_specs=field_specs,
            part2_columns=part2_columns,
        )

    def _parse_kis_master(
        self,
        *,
        url: str,
        zip_name: str,
        mst_name: str,
        part2_suffix_len: int,
        field_specs: list[int],
        part2_columns: list[str],
    ) -> pd.DataFrame:
        """Download, extract, and parse a KIS .mst master file."""
        ssl._create_default_https_context = ssl._create_unverified_context

        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = os.path.join(temp_dir, zip_name)
            mst_path = os.path.join(temp_dir, mst_name)
            tmp_fil1 = os.path.join(temp_dir, f"{mst_name}_part1.tmp")
            tmp_fil2 = os.path.join(temp_dir, f"{mst_name}_part2.tmp")

            try:
                urllib.request.urlretrieve(url, zip_path)
            except Exception as e:
                warnings.warn(f"Failed to download KIS master ({mst_name}): {str(e)}")
                return pd.DataFrame()

            try:
                with zipfile.ZipFile(zip_path, "r") as zip_ref:
                    zip_ref.extractall(temp_dir)
            except Exception as e:
                warnings.warn(f"Failed to extract KIS master ({mst_name}): {str(e)}")
                return pd.DataFrame()

            try:
                with open(tmp_fil1, mode="w", encoding="utf-8") as wf1, open(tmp_fil2, mode="w", encoding="utf-8") as wf2:
                    with open(mst_path, mode="r", encoding="cp949") as f:
                        for row in f:
                            rf1 = row[0:len(row) - part2_suffix_len]
                            rf1_1 = rf1[0:9].rstrip()
                            rf1_2 = rf1[9:21].rstrip()
                            rf1_3 = rf1[21:].strip()
                            wf1.write(f"{rf1_1},{rf1_2},{rf1_3}\n")

                            rf2 = row[-part2_suffix_len:]
                            wf2.write(rf2)

                part1_columns = ['단축코드', '표준코드', '한글명']
                df1 = pd.read_csv(tmp_fil1, header=None, names=part1_columns, encoding='utf-8')
                df2 = pd.read_fwf(tmp_fil2, widths=field_specs, names=part2_columns)
                df = pd.merge(df1, df2, how='outer', left_index=True, right_index=True)
                return df
            except Exception as e:
                warnings.warn(f"Failed to parse KIS master ({mst_name}): {str(e)}")
                return pd.DataFrame()

    def fetch_ohlcv(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data using Korea Investment API.
        
        Uses the inquire-daily-itemchartprice API endpoint which supports
        date range queries (up to 100 days per call).
        
        Returns DataFrame with Korean column names (like pykrx) for consistency
        with standardization layer.
        """
        auth = self._get_auth()
        
        # API endpoint for daily item chart price
        # Based on /domestic_stock/inquire_daily_itemchartprice
        api_path = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        tr_id = "FHKST03010100"
        
        # Format dates as YYYYMMDD
        start_str = start_date.replace("-", "")
        end_str = end_date.replace("-", "")
        
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",  # J=주식, ETF
            "FID_INPUT_ISCD": ticker.zfill(6),
            "FID_INPUT_DATE_1": start_str,
            "FID_INPUT_DATE_2": end_str,
            "FID_PERIOD_DIV_CODE": "D",  # D=일봉
            "FID_ORG_ADJ_PRC": "0" if adjusted else "1",  # 0=수정주가, 1=원주가
        }
        
        try:
            result = auth.fetch_api(api_path, tr_id, params)
            
            # The API returns output2 as array of daily data
            if "output2" not in result:
                return pd.DataFrame()
            
            df = pd.DataFrame(result["output2"])
            
            if df.empty:
                return pd.DataFrame()
            
            # Map API column names to Korean names (matching pykrx format)
            # API columns: stck_bsop_date, stck_oprc, stck_hgpr, stck_lwpr, stck_clpr, acml_vol, acml_tr_pbmn
            column_mapping = {
                "stck_bsop_date": "날짜",
                "stck_oprc": "시가",
                "stck_hgpr": "고가",
                "stck_lwpr": "저가",
                "stck_clpr": "종가",
                "acml_vol": "거래량",
                "acml_tr_pbmn": "거래대금",
            }
            
            # Only map columns that exist
            rename_dict = {k: v for k, v in column_mapping.items() if k in df.columns}
            df = df.rename(columns=rename_dict)
            
            # Convert date to datetime index
            if "날짜" in df.columns:
                df["날짜"] = pd.to_datetime(df["날짜"], format="%Y%m%d")
                df = df.set_index("날짜")
                df = df.sort_index()
            
            return df
            
        except Exception as e:
            # Log error and return empty DataFrame (fail-fast will be handled by orchestrator)
            raise RuntimeError(f"Failed to fetch OHLCV for {ticker}: {str(e)}") from e
