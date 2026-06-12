# Data Dictionary and Collection Status (OracleDB)

## 1. Purpose
이 문서는 OracleDB 적재 파이프라인에서 수집/생성되는 데이터의 항목, 타입, 수집 소스, 제약조건, 사용 예제를 정리합니다.
대상 독자는 다른 개발자 및 AI agent이며, 본 문서만으로도 조회/적재/검증 코드를 작성할 수 있도록 데이터 계약을 명시합니다.

## 2. System Scope
- 저장소: OracleDB
- 기준 DDL: db.txt
- 외부 진입점 provider: CompositeProvider only
- 내부 의존 provider: pykrx, korea_investment, fdr, yfinance (CompositeProvider 내부에서만 사용)

## 3. Collection Sources

| Source | Provider (internal) | Primary Use | Current Role |
|---|---|---|---|
| pykrx | PykrxProvider | OHLCV, market cap (time-series) | 1st source for price and market cap |
| Korea Investment (KIS) master | KoreaInvestmentProvider | market cap snapshot fallback | fallback when pykrx market cap fails; derived from 기준가 x 상장주수 x 1000 |
| FinanceDataReader | FdrProvider | ticker list, OHLCV fallback | ticker universe + OHLCV fallback |
| Local master JSON | MasterJsonProvider | stock master canonical map | stock/industry mapping and shares outstanding |
| Yahoo Finance | YfinanceProvider | dividend history | STOCK_DIVIDEND source |

Notes:
- External entry point always uses CompositeProvider only.
- Internal fallback chain is managed inside CompositeProvider.

## 4. Output DataFrames (Collection Layer)

### 4.1 industry_df
Mapped target: STOCK_INDUSTRY

| Column | Pandas dtype (expected) | Oracle target type | Description | Source |
|---|---|---|---|---|
| INDUSTRY_CODE | object (str, len=10) | VARCHAR2(10) | Stable hash code from industry triplet | derived (IndustryLarge/IndustryMid/IndustrySmall) |
| LARGE_CLASS | object (str) | VARCHAR2(50) | Industry large class | master JSON |
| MEDIUM_CLASS | object (str) | VARCHAR2(50) | Industry medium class | master JSON |
| SMALL_CLASS | object (str) | VARCHAR2(50) | Industry small class | master JSON |

### 4.2 master_df
Mapped target: STOCK_MASTER

| Column | Pandas dtype (expected) | Oracle target type | Description | Source |
|---|---|---|---|---|
| TICKER | object (str, zero-padded) | VARCHAR2(10) | 6-digit ticker | master JSON |
| STOCK_NAME | object (str) | VARCHAR2(100) | stock name | master JSON |
| MARKET_CODE | object (str: KOSPI/KOSDAQ/KONEX/ETF/ETN) | VARCHAR2(10) | explicit market string code | master JSON |
| ASSET_TYPE | object (str: S/E/N) | CHAR(1) | S: stock, E: ETF, N: ETN | derived from market |
| INDUSTRY_CODE | object (str, len=10) | VARCHAR2(10) | FK to STOCK_INDUSTRY | derived |
| IS_LISTED | object (str: Y/N) | CHAR(1) | Y: listed, N: delisted | default Y |
| UPDATED_AT | datetime64[ns] | DATE | collection timestamp (UTC naive) | generated at runtime |

Not yet collected for DB target:
- LISTED_DATE
- DELISTED_DATE

### 4.3 price_df
Mapped target: DAILY_PRICE

| Column | Pandas dtype (expected) | Oracle target type | Description | Source/Rule |
|---|---|---|---|---|
| TICKER | object (str) | VARCHAR2(10) | ticker | from collection ticker |
| PRICE_DATE | datetime64[ns] | DATE | trading date | OHLCV index/date |
| OPEN_PRICE | float64 | NUMBER(10) | open price | OHLCV |
| HIGH_PRICE | float64 | NUMBER(10) | high price | OHLCV |
| LOW_PRICE | float64 | NUMBER(10) | low price | OHLCV |
| CLOSE_PRICE | float64 | NUMBER(10) | close price | OHLCV |
| ADJ_CLOSE | float64 | NUMBER(10,2) | adjusted close (currently same as close) | rule |
| VOLUME | float64 | NUMBER(15) | trading volume | OHLCV |
| MARKET_CAP | float64 | NUMBER(20) | market capitalization (KRW, won) | enrichment chain |
| RS_1M | float64 | NUMBER(6,2) | 1개월 상대강도(RS) | release/collect input (optional) |
| RS_3M | float64 | NUMBER(6,2) | 3개월 상대강도(RS) | release/collect input (optional) |
| RS_6M | float64 | NUMBER(6,2) | 6개월 상대강도(RS) | release/collect input (optional) |
| RS_12M | float64 | NUMBER(6,2) | 12개월 상대강도(RS) | release/collect input (optional) |
| RS_WEIGHTED | float64 | NUMBER(6,2) | 가중 상대강도(Weighted RS) | (RS_1M*4 + RS_3M*3 + RS_6M*2 + RS_12M*1) / 10 |

Market cap enrichment chain:
1. Native market-cap column from source data
2. pykrx market-cap time-series merge
3. CLOSE_PRICE x SharesOutstanding fill
4. KIS market-cap snapshot fallback (기준가 x 상장주수 x 1000)
5. final fallback to 0 only if still missing

### 4.4 dividend_df
Mapped target: STOCK_DIVIDEND

| Column | Pandas dtype (expected) | Oracle target type | Description | Source/Rule |
|---|---|---|---|---|
| TICKER | object (str) | VARCHAR2(10) | ticker | yfinance ticker mapping |
| EX_DIVIDEND_DATE | datetime64[ns] | DATE | ex-dividend date | yfinance |
| DIVIDEND_PER_SHARE | float64 | NUMBER(10,2) | cash dividend per share | yfinance |
| RECORD_DATE | datetime64[ns] or NaT | DATE | record date | currently null |
| PAYMENT_DATE | datetime64[ns] or NaT | DATE | payment date | currently null |
| DIVIDEND_TYPE | object (str: R/S) | CHAR(1) | R: regular, S: special/interim | default R |

## 5. Oracle Table Contract (Keys, Constraints, Nullability)

아래 규칙은 db.txt DDL을 기준으로 작성된 데이터 계약입니다.

### 5.1 STOCK_INDUSTRY
- PK: INDUSTRY_CODE
- NOT NULL: INDUSTRY_CODE, LARGE_CLASS, MEDIUM_CLASS, SMALL_CLASS
- FK: 없음

### 5.2 STOCK_MASTER
- PK: TICKER
- NOT NULL: TICKER, STOCK_NAME, MARKET_CODE, ASSET_TYPE, IS_LISTED, UPDATED_AT
- NULL 허용: INDUSTRY_CODE, LISTED_DATE, DELISTED_DATE
- DEFAULT: IS_LISTED='Y', UPDATED_AT=SYSDATE
- FK: INDUSTRY_CODE -> STOCK_INDUSTRY.INDUSTRY_CODE

### 5.3 DAILY_PRICE
- PK: TICKER + PRICE_DATE
- NOT NULL: TICKER, PRICE_DATE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, ADJ_CLOSE, VOLUME, MARKET_CAP
- NULL 허용: RS_1M, RS_3M, RS_6M, RS_12M, RS_WEIGHTED
- FK: TICKER -> STOCK_MASTER.TICKER

### 5.4 STOCK_DIVIDEND
- PK: TICKER + EX_DIVIDEND_DATE
- NOT NULL: TICKER, EX_DIVIDEND_DATE, DIVIDEND_PER_SHARE, DIVIDEND_TYPE, CREATED_AT
- NULL 허용: RECORD_DATE, PAYMENT_DATE
- DEFAULT: DIVIDEND_TYPE='R', CREATED_AT=SYSDATE
- FK: TICKER -> STOCK_MASTER.TICKER

### 5.5 ETF_COMPONENT
- PK: ETF_TICKER + COMPONENT_TICKER + BASE_DATE
- NOT NULL: ETF_TICKER, COMPONENT_TICKER, BASE_DATE, WEIGHT_PCT
- NULL 허용: SHARES_HELD
- FK: ETF_TICKER -> STOCK_MASTER.TICKER
- Collection status: not implemented

## 6. Collection Value to Oracle Value Mapping

| Logical field | Collection value | Oracle stored value |
|---|---|---|
| ASSET_TYPE | STOCK | S |
| ASSET_TYPE | ETF | E |
| ASSET_TYPE | ETN | N |
| IS_LISTED | LISTED | Y |
| IS_LISTED | DELISTED | N |

주의: 현재 파이프라인은 master_df 단계에서 이미 ASSET_TYPE, IS_LISTED를 각각 S/E/N, Y/N으로 생성합니다.

## 7. Data Format, Time, Unit, Precision Rules

### 7.1 Ticker format
- 표준 포맷: 6-digit zero-padded string (예: 005930)
- Oracle 컬럼 길이는 VARCHAR2(10)이지만, 현재 파이프라인 표준은 6자리 문자열입니다.

### 7.2 Date/time rule
- Oracle DATE로 저장되는 datetime은 timezone naive 값입니다.
- PRICE_DATE는 거래일 기준 날짜(시분초 의미 없음)로 취급합니다.
- UPDATED_AT은 실행 시점 UTC naive timestamp로 저장됩니다.

### 7.3 Numeric unit rule
- 가격 계열(OPEN/HIGH/LOW/CLOSE/ADJ_CLOSE): KRW 기준
- VOLUME: 거래량(주)
- MARKET_CAP: KRW 기준 시가총액

### 7.4 Precision and casting rule
- ADJ_CLOSE는 Oracle NUMBER(10,2) 제약을 따릅니다.
- VOLUME은 int로 변환 후 저장합니다.
- MARKET_CAP은 float로 변환 후 저장합니다.
- RS_1M/RS_3M/RS_6M/RS_12M/RS_WEIGHTED는 float로 변환 후 NUMBER(6,2)로 저장합니다.
- Oracle 제약 위반 방지를 위해 upsert 전 dtype 변환을 수행합니다.

### 7.5 Null policy
- DAILY_PRICE 핵심 시세 컬럼(TICKER, PRICE_DATE, OPEN/HIGH/LOW/CLOSE, ADJ_CLOSE, VOLUME, MARKET_CAP)은 NOT NULL입니다.
- MARKET_CAP이 최종까지 결측이면 0으로 채운 뒤 저장합니다.
- RS 컬럼은 NULL 허용이며, 릴리즈 자산에 컬럼이 없으면 경고 후 NULL로 적재합니다.
- STOCK_DIVIDEND의 RECORD_DATE, PAYMENT_DATE는 현재 null 허용 정책입니다.

## 8. Quality Metrics Definition (Run-level)

현재 품질 지표는 실행 1회(run-level) 기준으로 집계됩니다.

| Metric | Meaning | Scope |
|---|---|---|
| market_cap_missing_before | 보강 전 MARKET_CAP 결측 행 수 | current run price_df |
| market_cap_missing_after_enrichment | 보강 로직 적용 후 결측 행 수 | current run price_df |
| market_cap_zero_final | 최종 0 대체 행 수 | current run price_df |
| price_row_count | 최종 price_df 행 수 | current run price_df |
| dividend_row_count | 최종 dividend_df 행 수 | current run dividend_df |

## 9. Usage Examples (OracleDB)

### 9.1 Environment variables
아래 환경 변수는 Oracle 연결에 필요합니다.
- OCI_DB_USER
- OCI_DB_PW
- OCI_DB_DSN
- OCI_WALLET (optional, base64 zip)
- OCI_WALLET_PW (optional)

### 9.2 Dry-run collection only
DB upsert 없이 수집 결과와 리포트만 생성합니다.

python scripts/sync_oracle.py --mode daily --lookback-days 10 --dry-run --no-send-report

### 9.3 Upsert daily mode
일간 모드로 수집 후 OracleDB에 업서트합니다.

python scripts/sync_oracle.py --mode daily --lookback-days 10 --batch-size 2000 --no-send-report

### 9.4 Upsert explicit date range
지정 구간만 적재합니다.

python scripts/sync_oracle.py --mode range --start-date 2026-06-01 --end-date 2026-06-05 --batch-size 2000 --no-send-report

### 9.5 SQL validation examples
행 수 확인:
SELECT COUNT(*) FROM DAILY_PRICE;

일자 범위 확인:
SELECT MIN(TRUNC(PRICE_DATE)), MAX(TRUNC(PRICE_DATE)) FROM DAILY_PRICE;

중복 키 검사(0이어야 정상):
SELECT TICKER, PRICE_DATE, COUNT(*)
FROM DAILY_PRICE
GROUP BY TICKER, PRICE_DATE
HAVING COUNT(*) > 1;

## 10. Sample Records

아래 샘플은 실제 수집 결과 포맷을 이해하기 위한 예시입니다.

<!-- AUTO-SAMPLES-START -->

### 10.1 industry_df sample

```json
{
  "INDUSTRY_CODE": "83C6C8DAC3",
  "LARGE_CLASS": "IT",
  "MEDIUM_CLASS": "디스플레이",
  "SMALL_CLASS": "디스플레이 및 관련부품"
}
```

### 10.2 master_df sample

```json
{
  "TICKER": "005930",
  "STOCK_NAME": "삼성전자",
  "MARKET_CODE": "KOSPI",
  "ASSET_TYPE": "S",
  "INDUSTRY_CODE": "BB9DF50070",
  "IS_LISTED": "Y",
  "UPDATED_AT": "2026-06-05T07:26:26.672479"
}
```

### 10.3 price_df sample

```json
{
  "TICKER": "005930",
  "PRICE_DATE": "2026-06-05T00:00:00",
  "OPEN_PRICE": 12665,
  "HIGH_PRICE": 12665,
  "LOW_PRICE": 11720,
  "CLOSE_PRICE": 11725,
  "ADJ_CLOSE": 11725,
  "VOLUME": 327994,
  "MARKET_CAP": 500000000000.0
}
```

### 10.4 dividend_df sample

```json
{
  "TICKER": "005930",
  "EX_DIVIDEND_DATE": "2026-03-31T00:00:00",
  "DIVIDEND_PER_SHARE": 361.0,
  "RECORD_DATE": null,
  "PAYMENT_DATE": null,
  "DIVIDEND_TYPE": "R"
}
```

### 10.5 quality_metrics sample

```json
{
  "market_cap_missing_before": 0,
  "market_cap_missing_after_enrichment": 0,
  "market_cap_zero_final": 0,
  "price_row_count": 2440,
  "dividend_row_count": 41
}
```

<!-- AUTO-SAMPLES-END -->

## 11. DB Coverage Status (db.txt 기준)

| DB Table | Collection status | Notes |
|---|---|---|
| STOCK_INDUSTRY | Implemented | industry_df ready |
| STOCK_MASTER | Implemented (partial) | LISTED_DATE, DELISTED_DATE pending |
| DAILY_PRICE | Implemented | market-cap fallback chain applied |
| STOCK_DIVIDEND | Implemented (partial) | RECORD_DATE, PAYMENT_DATE pending |
| ETF_COMPONENT | Not implemented | source/collector pending |

## 12. Current Limitations
- pykrx market-cap API can fail intermittently for some ticker/date combinations.
- KIS fallback currently uses snapshot-level cap from master files, not full daily time-series.
- ETF and 일부 ticker group still require additional source hardening for fully stable daily market-cap coverage.
- STOCK_DIVIDEND의 RECORD_DATE, PAYMENT_DATE는 현재 미수집 상태입니다.

## 13. Next Actions
1. Add source contribution metrics per row (pykrx/kis/calc/fallback).
2. Implement ETF_COMPONENT collection.
3. Evaluate KIS endpoint for daily market-cap time-series and replace snapshot fallback where possible.
4. Extend STOCK_DIVIDEND with RECORD_DATE and PAYMENT_DATE when a stable source is confirmed.
