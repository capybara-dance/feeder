# Data Dictionary and Collection Status

## 1. Purpose
이 문서는 OracleDB 적재 전 단계에서 수집/생성되는 데이터의 항목, 타입, 수집 소스, 구현 상태를 정리합니다.

## 2. Collection Sources

| Source | Provider (internal) | Primary Use | Current Role |
|---|---|---|---|
| pykrx | `PykrxProvider` | OHLCV, market cap (time-series) | 1st source for price and market cap |
| Korea Investment (KIS) master | `KoreaInvestmentProvider` | market cap snapshot fallback | 2nd fallback when pykrx market cap fails |
| FinanceDataReader | `FdrProvider` | ticker list, OHLCV fallback | ticker universe + OHLCV fallback |
| Local master JSON | `MasterJsonProvider` | stock master canonical map | stock/industry mapping and shares outstanding |

Notes:
- External entry point always uses `CompositeProvider` only.
- Internal fallback chain is managed inside `CompositeProvider`.

## 3. Output DataFrames (Collection Layer)

### 3.1 industry_df
Mapped target: `STOCK_INDUSTRY`

| Column | Pandas dtype (expected) | Oracle target type | Description | Source |
|---|---|---|---|---|
| INDUSTRY_CODE | object (str, len=10) | VARCHAR2(10) | Stable hash code from industry triplet | derived (`IndustryLarge/IndustryMid/IndustrySmall`) |
| LARGE_CLASS | object (str) | VARCHAR2(50) | Industry large class | master JSON |
| MEDIUM_CLASS | object (str) | VARCHAR2(50) | Industry medium class | master JSON |
| SMALL_CLASS | object (str) | VARCHAR2(50) | Industry small class | master JSON |

### 3.2 master_df
Mapped target: `STOCK_MASTER`

| Column | Pandas dtype (expected) | Oracle target type | Description | Source |
|---|---|---|---|---|
| TICKER | object (str, zero-padded) | VARCHAR2(10) | 6-digit ticker | master JSON |
| STOCK_NAME | object (str) | VARCHAR2(100) | stock name | master JSON |
| MARKET_CODE | object (str: KOSPI/KOSDAQ/KONEX/ETF/ETN) | VARCHAR2(10) | explicit market string code | master JSON |
| ASSET_TYPE | object (str: STOCK/ETF/ETN) | CHAR(1) | asset class label for readability | derived from market |
| INDUSTRY_CODE | object (str, len=10) | VARCHAR2(10) | FK to industry table | derived |
| IS_LISTED | object (str: LISTED/DELISTED) | CHAR(1) | listing status (currently collected as LISTED) | default LISTED |
| UPDATED_AT | datetime64[ns] | DATE | collection timestamp | generated at runtime |

Code mapping for Oracle upsert:
- `ASSET_TYPE`: STOCK->`S`, ETF->`E`, ETN->`N`
- `IS_LISTED`: LISTED->`Y`, DELISTED->`N`

Not yet collected for DB target:
- `LISTED_DATE`
- `DELISTED_DATE`

### 3.3 price_df
Mapped target: `DAILY_PRICE`

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
| MARKET_CAP | float64 | NUMBER(20) | market capitalization (KRW, 원) | enrichment chain |

Market cap enrichment chain:
1. Native market-cap column from source data
2. pykrx market-cap time-series merge
3. `CLOSE_PRICE * SharesOutstanding` fill
4. KIS market-cap snapshot fallback
5. final fallback to 0 only if still missing

Quality metrics currently emitted:
- `market_cap_missing_before`
- `market_cap_missing_after_enrichment`
- `market_cap_zero_final`
- `price_row_count`

## 4. Sample Records

아래 샘플은 실제 수집 결과 포맷을 이해하기 위한 예시입니다.

<!-- AUTO-SAMPLES-START -->

### 4.1 industry_df sample

```json
{
  "INDUSTRY_CODE": "83C6C8DAC3",
  "LARGE_CLASS": "IT",
  "MEDIUM_CLASS": "디스플레이",
  "SMALL_CLASS": "디스플레이 및 관련부품"
}
```

### 4.2 master_df sample

```json
{
  "TICKER": "005930",
  "STOCK_NAME": "삼성전자",
  "MARKET_CODE": "KOSPI",
  "ASSET_TYPE": "STOCK",
  "INDUSTRY_CODE": "BB9DF50070",
  "IS_LISTED": "LISTED",
  "UPDATED_AT": "2026-06-05T07:26:26.672479"
}
```

### 4.3 price_df sample

```json
{
  "TICKER": "0000Z0",
  "PRICE_DATE": "2026-06-05T00:00:00",
  "OPEN_PRICE": 12665,
  "HIGH_PRICE": 12665,
  "LOW_PRICE": 11720,
  "CLOSE_PRICE": 11725,
  "ADJ_CLOSE": 11725,
  "VOLUME": 327994,
  "MARKET_CAP": 5.0
}
```

### 4.4 quality_metrics sample

```json
{
  "market_cap_missing_before": 0,
  "market_cap_missing_after_enrichment": 0,
  "market_cap_zero_final": 0,
  "price_row_count": 2440
}
```

<!-- AUTO-SAMPLES-END -->

## 5. DB Coverage Status (`db.txt` 기준)

| DB Table | Collection status | Notes |
|---|---|---|
| STOCK_INDUSTRY | Implemented | `industry_df` ready |
| STOCK_MASTER | Implemented (partial) | `LISTED_DATE`, `DELISTED_DATE` pending |
| DAILY_PRICE | Implemented | market-cap fallback chain applied |
| STOCK_DIVIDEND | Not implemented | source/collector pending |
| ETF_COMPONENT | Not implemented | source/collector pending |

## 6. Current Limitations
- pykrx market-cap API can fail intermittently for some ticker/date combinations.
- KIS fallback currently uses snapshot-level cap from master files, not full daily time-series.
- ETF and 일부 ticker group still require additional source hardening for fully stable daily market-cap coverage.

## 7. Next Actions
1. Add source contribution metrics per row (`pykrx/kis/calc/fallback`).
2. Implement `STOCK_DIVIDEND` collection.
3. Implement `ETF_COMPONENT` collection.
4. Evaluate KIS endpoint for daily market-cap time-series and replace snapshot fallback where possible.
