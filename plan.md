# OracleDB 업데이트 파이프라인 구현 계획

## 1) 배경 및 목표
- 기존(old) 파이프라인은 약 10년치 시세/지표를 수집해 Parquet + meta.json을 생성하고 GitHub Release로 배포한다.
- 신규 파이프라인은 동일/유사한 데이터 수집·정제·지표 계산 로직을 재사용하되, 파일 릴리즈 대신 OracleDB 테이블에 직접 적재(Upsert)한다.
- DB는 현재 비어 있으므로, `db.txt` DDL 기준으로 초기 적재 + 이후 증분 업데이트 전략이 필요하다.

## 2) 기존 코드 분석 요약 (old 기준)

### 핵심 엔트리포인트
- `old/scripts/generate_cache.py`
  - 파이프라인 시작점(CLI).
  - provider 선택(composite/pykrx/korea_investment/fdr) 후 `run_cache_build` 호출.
- `old/capybara_fetcher/orchestrator.py`
  - 전체 수집/정제/지표 계산/결과 저장 오케스트레이션 담당.
  - 현재는 최종 결과를 Parquet와 메타 JSON으로 저장.

### 데이터 처리 흐름
- Universe/마스터: provider에서 종목 목록 + 마스터 로딩.
- 벤치마크(069500) 로딩 후 MansfieldRS 계산 기준 준비.
- 종목별 OHLCV 수집 -> 표준화(`standardize_ohlcv`) -> 지표 계산(`compute_features`).
- 전체 concat/sort 후(현재 구현) Parquet 저장.
- 옵션으로 업종 강도 프레임 계산 후 별도 Parquet 저장.

### 확장 포인트
- `DataProvider` 프로토콜(`old/capybara_fetcher/provider.py`)이 소스 추상화를 제공하므로 재사용 가치가 높다.
- 저장 계층(현재 파일 저장)을 DB 저장 계층으로 분리하면 파이프라인 재사용이 가능하다.
- `old/scripts/validate_data.py`의 품질 검증 아이디어를 DB 검증 쿼리로 전환 가능하다.

## 3) DB 스키마 해석 (`db.txt`)

### 테이블
- `STOCK_INDUSTRY`: 업종 마스터(PK: `INDUSTRY_CODE`)
- `STOCK_MASTER`: 종목 마스터(PK: `TICKER`, FK: `INDUSTRY_CODE`)
- `DAILY_PRICE`: 일별 시세(PK: `TICKER, PRICE_DATE`)
- `STOCK_DIVIDEND`: 배당(PK: `TICKER, EX_DIVIDEND_DATE`)
- `ETF_COMPONENT`: ETF 구성종목(PK: `ETF_TICKER, COMPONENT_TICKER, BASE_DATE`)

### 현재 old 데이터와 직접 매핑 가능한 범위
- 즉시 매핑 가능(우선 구현):
  - `STOCK_INDUSTRY`, `STOCK_MASTER`, `DAILY_PRICE`
- 별도 데이터 소스 필요(2차 구현):
  - `STOCK_DIVIDEND`, `ETF_COMPONENT`

## 4) 타깃 아키텍처
- 수집/가공 계층: 기존 `provider + standardize + indicators + orchestrator` 재사용.
- Provider 정책: 외부 진입점(CLI/스케줄러/서비스)에서는 `CompositeProvider`만 사용하고, `pykrx/korea_investment/fdr`는 Composite 내부 구현으로만 사용한다.
- 저장 계층: Oracle 전용 Writer 모듈 추가.
- 실행 계층: 파일 릴리즈용 CLI와 분리된 DB 동기화용 CLI 추가.
- 운영 계층: 초기 적재(full load) + 증분 적재(incremental) 모드 제공.

## 5) 구현 범위 제안

### Phase 1 (MVP) - 반드시 구현
- `STOCK_INDUSTRY` upsert
- `STOCK_MASTER` upsert
- `DAILY_PRICE` upsert (10년치 초기 적재 + 일 단위 증분)
- 실행 결과 요약 로그(처리건수/에러건수/소요시간)

### Phase 2 - 확장
- `STOCK_DIVIDEND` 적재
- `ETF_COMPONENT` 적재
- 업종 강도/파생 지표 저장 테이블 추가 여부 검토(현 스키마엔 없음)

## 6) 상세 설계

### 6.1 파일/모듈 구조 (신규)
- `capybara_fetcher/db/oracle_client.py`
  - 연결 생성, 트랜잭션/커밋/롤백, `executemany` 유틸.
- `capybara_fetcher/db/sql_templates.py`
  - 테이블별 `MERGE` SQL 템플릿 관리.
- `capybara_fetcher/db/mappers.py`
  - DataFrame -> DB 컬럼 매핑/타입 변환(날짜/숫자/NULL).
- `capybara_fetcher/db/repository.py`
  - `upsert_stock_industry`, `upsert_stock_master`, `upsert_daily_price`.
- `scripts/sync_oracle.py`
  - 신규 CLI 엔트리포인트.

### 6.2 실행 모드
- `--mode full`
  - 시작일~종료일까지 전체 적재(초기 구축용).
- `--mode incremental`
  - DB 내 마지막 `PRICE_DATE`를 읽어 다음 영업일부터 적재.
- `--dry-run`
  - 실제 반영 없이 예상 건수/쿼리 계획만 출력.

### 6.3 업서트 전략
- 모든 테이블은 PK 기준 `MERGE INTO` 사용.
- 배치 단위 `executemany` 처리(예: 2,000~10,000건)로 메모리/속도 균형.
- `DAILY_PRICE`는 `(TICKER, PRICE_DATE)` 충돌 시 가격/거래량/시총 최신값으로 업데이트.

### 6.4 데이터 매핑 규칙
- `STOCK_INDUSTRY.INDUSTRY_CODE`
  - `IndustryLarge/IndustryMid/IndustrySmall` 조합으로 안정적 코드 생성(예: 해시/정규화 키).
- `STOCK_MASTER`
  - `TICKER <- Code`
  - `STOCK_NAME <- Name`
  - `MARKET_CODE`: `KOSPI`/`KOSDAQ`/`KONEX` 같은 명시적 문자열 코드로 저장
  - `ASSET_TYPE`: 주식=S, ETF=E, ETN=N (판별 규칙 필요)
  - `IS_LISTED`: 기본 Y
  - `UPDATED_AT`: 적재 시각
- `DAILY_PRICE`
  - `OPEN/HIGH/LOW/CLOSE/VOLUME`는 표준화 컬럼 사용.
  - `ADJ_CLOSE`: 우선 `Close`와 동일값으로 저장(추후 분리 가능).
  - `MARKET_CAP`: 소스 부재 시 NULL 허용 검토 필요(`db.txt`는 NOT NULL이므로 산출식 또는 임시 정책 필요).

## 7) 사전 확인이 필요한 쟁점
- `DAILY_PRICE.MARKET_CAP`가 NOT NULL인데, 기존 파이프라인에서 안정적 시총 컬럼이 항상 존재하는지 확인 필요.
- KONEX/ETN 코드 매핑 규칙(소스별 일관성) 확정 필요.
- `INDUSTRY_CODE` 생성 방식(가독성 vs 안정성) 확정 필요.
- 배당/ETF 구성 데이터의 수집 소스 및 주기 확정 필요.

## 8) 테스트/검증 계획
- 단위 테스트
  - 매퍼 테스트: 컬럼 변환/NULL 처리/코드 매핑.
  - SQL 생성 테스트: `MERGE` 파라미터 바인딩 검증.
- 통합 테스트
  - 빈 DB 대상 full load -> 건수 검증.
  - incremental 재실행 -> 중복 없이 업데이트되는지 검증.
- 품질 검증 SQL
  - PK 중복 0건 확인.
  - 일자별 레코드 수 급감 탐지.
  - 종목 수/날짜 범위가 기대치와 일치하는지 확인.

## 9) 단계별 작업 계획
1. 기존 파이프라인에서 파일 저장 직전 DataFrame 인터페이스 고정(입출력 계약 정의).
2. Oracle 연결/설정 계층 구현(`.env` 기반 DSN, 사용자, 비밀번호, 배치 크기).
3. `STOCK_INDUSTRY`/`STOCK_MASTER` upsert 구현 후 소량 데이터로 검증.
4. `DAILY_PRICE` upsert 구현 + full load 실행.
5. incremental 모드 구현(마지막 적재일 기반).
6. 장애 복구/재시도/로그 보강.
7. CI 혹은 스케줄러(cron/GitHub Actions)로 DB 동기화 잡 분리.

## 10) 운영 가이드 초안
- 권장 순서: INDUSTRY -> MASTER -> DAILY_PRICE
- 트랜잭션 경계:
  - 마스터 계층은 테이블 단위 커밋.
  - 시세는 배치 단위 커밋 + 실패 배치 로깅.
- 장애 대응:
  - 실패 시 마지막 성공 일자부터 재시작.
  - 드라이런으로 예상 처리량 점검 후 본 실행.

## 11) 완료 기준 (Definition of Done)
- 빈 OracleDB에 10년치 full load 성공.
- 같은 기간 재실행 시 중복 적재 없이 upsert 동작 확인.
- 하루치 incremental 실행이 정상 동작.
- 실행 로그에서 처리/실패 건수 및 수행시간 확인 가능.
- 핵심 테스트(매퍼/레포지토리/통합) 통과.

## 12) 단계별 실행 플랜 (작게 시작)

### Step 0. 작업 원칙 고정
- `old/`는 참조 전용으로만 사용하고 수정하지 않는다.
- 신규 코드는 `old/` 밖에만 생성한다.
- 각 단계는 독립 실행/검증 가능해야 다음 단계로 진행한다.

### Step 1. 수집 전용 MVP (DB write 없음)  
목표: DB 적재 전에, 필요한 데이터를 안정적으로 수집/표준화해서 메모리(DataFrame)로 확보한다.

- 구현 범위
  - 신규 엔트리포인트 초안: `scripts/sync_oracle.py` (수집만 수행)
  - 신규 모듈 초안: `capybara_fetcher/pipeline/collect.py`
  - 수행 기능
    - `CompositeProvider` 단일 생성 및 주입
    - 종목 마스터 로드
    - 티커 목록 조회
    - 티커별 OHLCV 수집 + 표준화
    - (선택) 지표 계산까지 포함해 `DAILY_PRICE` 입력 후보 프레임 생성

- 결과물(출력 계약)
  - `industry_df` 입력 후보: `INDUSTRY_CODE`, `LARGE_CLASS`, `MEDIUM_CLASS`, `SMALL_CLASS`
  - `master_df` 입력 후보: `TICKER`, `STOCK_NAME`, `MARKET_CODE`(문자열), `ASSET_TYPE`, `INDUSTRY_CODE`, `IS_LISTED`, `UPDATED_AT`
  - `price_df` 입력 후보: `TICKER`, `PRICE_DATE`, `OPEN_PRICE`, `HIGH_PRICE`, `LOW_PRICE`, `CLOSE_PRICE`, `ADJ_CLOSE`, `VOLUME`, `MARKET_CAP`

- 검증 기준
  - CLI 1회 실행 시 세 프레임 row 수/컬럼 목록이 로그로 출력된다.
  - 최소 N개 티커(예: 50) 제한 실행이 성공한다.
  - `MARKET_CODE`는 숫자가 아닌 문자열(`KOSPI`/`KOSDAQ`/`KONEX`)로 유지된다.

### Step 2. 매퍼 계층 구현 (DB 파라미터 변환)
목표: DataFrame을 Oracle 바인딩 가능한 레코드(dict/tuple)로 변환한다.

- 구현 범위
  - `capybara_fetcher/db/mappers.py`
  - 날짜/숫자/NULL 처리 규칙 고정
  - `INDUSTRY_CODE` 생성 규칙 구현

- 검증 기준
  - 단위 테스트로 타입 변환/결측 처리/코드 생성이 재현 가능하다.

### Step 3. Oracle 저장소 계층 구현 (MERGE)
목표: 테이블별 upsert SQL과 배치 실행을 구현한다.

- 구현 범위
  - `capybara_fetcher/db/oracle_client.py`
  - `capybara_fetcher/db/sql_templates.py`
  - `capybara_fetcher/db/repository.py`

- 검증 기준
  - 소량 샘플(예: 1000행) upsert 성공
  - 재실행 시 중복 없이 update 동작

### Step 4. 엔드투엔드 연결 (수집 -> 매핑 -> 저장)
목표: `scripts/sync_oracle.py`에서 full/incremental 모드까지 연결한다.

### Step 5. 운영 안정화
- 재시도/배치 실패 로그/요약 리포트/검증 SQL 자동화

## 13) 바로 다음 실행 항목 (Next)
1. Step 1 착수: `scripts/sync_oracle.py` + `capybara_fetcher/pipeline/collect.py` 스캐폴드 생성
2. 날짜 인자/테스트 제한 인자(`--test-limit`) 먼저 구현하고 provider 선택 인자는 노출하지 않음
3. DB 쓰기 없이 `industry_df`, `master_df`, `price_df`의 shape/컬럼 로그 출력까지 완료

## 14) Provider 캡슐화 규칙
- 외부 인터페이스에서 provider 이름 선택(`--provider`)을 허용하지 않는다.
- 수집 파이프라인은 항상 `CompositeProvider`를 사용한다.
- `pykrx/korea_investment/fdr`는 `CompositeProvider` 내부 private 구성요소로만 접근한다.
- 장애 대응/폴백 전략은 Composite 내부 정책으로만 관리한다.

## 15) 문서화 강제 규칙
- 새로운 기능 추가 또는 기존 기능 동작 변경이 발생하면 같은 작업 세션에서 반드시 Markdown 문서를 갱신한다.
- 최소 필수 반영 문서: `plan.md` (진행상태, 결정사항, 다음 작업)
- 사용자 사용법/실행 방법/운영 절차가 바뀐 경우: `README.md`도 함께 갱신한다.
- Handoff에는 문서 반영 내역(수정 파일 경로)을 반드시 포함한다.

## 16) 최근 반영 메모
- 루트 `requirements.txt`를 생성해 Step 1 수집부 실행 필수 패키지(`pandas`, `pykrx`, `finance-datareader`)를 명시했다.
- `scripts/run_collection_report.py`를 추가해 수집부 테스트 결과/샘플을 HTML(`reports/collection_test_report.html`)로 생성하고 텔레그램 문서 전송까지 자동화했다.
- `.github/workflows/run_collection_report.yml`을 추가해 `scripts/run_collection_report.py` 변경 커밋 시 자동 실행되도록 했고, `workflow_dispatch` 수동 실행도 지원하도록 구성했다. 또한 `.env`의 각 키를 동일한 이름의 Repository Secret으로 저장해 job `env`에 직접 매핑하도록 반영했다.
- 시총 보강 로직(원천 시총 + pykrx 시총 병합 + `Close*SharesOutstanding` 보정 + fallback 0)을 수집 파이프라인에 적용하고, 품질 지표를 리포트에 출력하도록 반영했다.
- `korea_investment` 마스터 기반 시총 snapshot fallback을 `CompositeProvider` 내부에 구현했다(pykrx 실패 시 사용).
- 최근 테스트(`test-limit=10`) 기준 리포트 지표에서 `market_cap_zero_final`이 0(0.00%)으로 개선됨.
- 데이터 항목 타입/수집 소스/구현 상태를 정리한 문서 `docs/data_dictionary.md`를 추가했다.
- `scripts/update_data_dictionary_samples.py`를 추가해 최근 수집 결과로 `docs/data_dictionary.md` 샘플 블록을 자동 갱신하도록 구성했다.
- KIS 시총 fallback을 원본 `시가총액` 필드 대신 `기준가 × 상장주수 × 1000` 재계산값으로 사용하도록 바꿨다.
- `capybara_fetcher/providers/yfinance_provider.py`를 추가하고 `CompositeProvider`에 연결해 배당 조회 경로를 구현했다.
- `collect_data` 결과에 `dividend_df`를 추가해 배당 데이터(`STOCK_DIVIDEND` 대상 컬럼 매핑)를 수집하도록 확장했다.
- `capybara_fetcher/db/` 모듈(`oracle_client.py`, `sql_templates.py`, `repository.py`)을 추가해 현재 구현 완료 데이터(`STOCK_INDUSTRY`, `STOCK_MASTER`, `DAILY_PRICE`, `STOCK_DIVIDEND`)를 OracleDB에 MERGE upsert할 수 있도록 구현했다.
- `scripts/sync_oracle.py`를 collection-only에서 실제 upsert 실행기로 확장하고, `--dry-run`, `--batch-size` 옵션을 추가했다.
- `scripts/run_collection_report.py`를 DB 샘플 리포트용으로 확장해 `STOCK_INDUSTRY`, `STOCK_MASTER`, `DAILY_PRICE`, `STOCK_DIVIDEND`, `ETF_COMPONENT`의 row count/샘플 데이터를 HTML로 생성하고 텔레그램 전송하도록 반영했다.
- `.github/workflows/run_collection_report.yml`에 `OCI_DB_USER`, `OCI_DB_DSN` 시크릿 매핑을 추가해 GitHub Actions에서도 DB 샘플 리포트를 생성/전송할 수 있도록 반영했다.
- `scripts/sync_oracle.py`에 실행 모드(`daily`, `full-10y`, `range`)를 추가했다. `daily` 모드는 기본적으로 당일 데이터를 적재하며, DB 조회 기준 `오늘-10일` 영업일 누락 데이터가 있으면 해당 날짜도 함께 재수집/업데이트하도록 반영했다.
- `.github/workflows/sync_oracle.yml`을 추가해 매일 21:00 KST 자동 실행 + 수동 트리거를 지원하도록 구성했다.
- `.github/workflows/sync_oracle.yml`에 `actions/upload-artifact@v4` 단계를 추가해 `reports/sync_oracle_report.html`을 실행 결과 artifact로 보관하도록 반영했다(실패 시에도 `if: always()`로 업로드 시도).
- `scripts/sync_oracle.py`의 HTML 리포트 본문(제목/섹션/지표 라벨/상태)을 한국어로 변경했다.

## 17) MARKET_CAP 0 문제 해결 방안

### 원인 요약
- 수집 표준화 단계에서 `MarketCap` 컬럼이 없으면 결측으로 남는다.
- price 변환 단계에서 결측 `MARKET_CAP`를 일괄 0으로 채우고 있어 결과적으로 시총이 0으로 고정된다.
- 추가 확인: `pykrx` 시총 조회 API가 일부 종목/구간에서 실패(응답 파싱 오류/티커 해석 오류)하여 결측이 반복 발생한다.

### 목표
- `MARKET_CAP`를 가능한 실제값으로 채우고, 불가한 경우에만 정책적으로 보정한다.
- 0 채움은 마지막 fallback으로만 사용하고, 품질 지표(결측률/0비율)를 반드시 기록한다.

### 단계별 조치
1. 단기 조치
  - `fillna(0)`를 즉시 제거하고 결측 상태를 유지한다.
  - 실행 로그에 `MARKET_CAP` 결측률/0비율을 출력한다.
2. 1차 보강
  - `pykrx` 시세 수집 시 동일 구간의 시총 데이터를 추가 조회해 날짜 기준으로 병합한다.
  - 병합 후에도 결측인 행만 다음 단계 보정 대상으로 분리한다.
3. 2차 보강
  - `master`의 `SharesOutstanding`이 존재하는 종목은 `CLOSE_PRICE * SharesOutstanding`으로 시총을 계산한다.
  - 계산값 적용 여부를 별도 플래그(예: `MARKET_CAP_SOURCE`)로 내부 로그에 남긴다.
4. fallback 정책
  - 위 두 단계 후에도 결측인 경우에만 0 또는 정책값을 적용한다.
  - 최종 0 적용 비율이 임계치(예: 5%)를 초과하면 경고 또는 실패 처리한다.
5. 대체 API 도입(후속)
  - `korea_investment` API를 시총 조회 대체 소스로 검증한다.

## 18) TODO
- pykrx 인증용 환경 변수(`KRX_ID`, `KRX_PW`)를 `.env` 또는 실행 환경에 설정하고, `005930`, `069500` 기준으로 pykrx 시총 조회 정상 동작을 재검증한다.
  - `CompositeProvider` 내부에서 `pykrx -> korea_investment` 순으로 시총 조회 fallback 체인을 구성한다.
  - 소스별 성공/실패 카운트를 리포트 지표로 기록한다.

### 검증 기준
- 샘플 실행(test-limit 50)에서 `MARKET_CAP=0` 비율이 기존 대비 유의미하게 감소한다.
- 시총이 채워진 상위 종목(KOSPI/KOSDAQ/ETF) 샘플 10건을 리포트에 표시한다.
- 리포트/로그에 `market_cap_missing_before`, `market_cap_missing_after`, `market_cap_zero_final` 지표를 출력한다.

## 19) 예외 삼킴 개선 및 관측성 강화 계획

### 배경
- 현재 `CompositeProvider` 등 일부 경로에서 `except Exception: pass` 패턴이 존재해, 소스 장애 원인 추적이 어렵다.
- 배치 실행 시 특정 티커에서 오류가 발생해도 원인/건수/영향 범위를 구조적으로 파악하기 어렵다.

### 목표
- 예외를 무시하지 않고 최소한 구조화 로그로 남긴다.
- 티커 단위 실패를 격리해 전체 수집은 지속하되, 실패 통계와 원인을 리포트 가능하게 만든다.

### 단계별 조치
1. 예외 처리 규칙 정비
  - `except Exception: pass`를 제거하고 `logger.warning` 또는 `logger.exception`으로 대체한다.
  - 로그 필드에 `provider`, `ticker`, `stage`, `error_type`, `message`를 포함한다.
2. 실패 격리
  - 병렬/직렬 수집 모두에서 티커 단위 try-catch를 적용한다.
  - 실패 티커는 스킵하고 나머지 티커 수집은 계속 수행한다.
3. 메트릭 집계
  - 소스별 성공/실패 카운트(`pykrx`, `fdr`, `korea_investment`, `yfinance`)를 `quality_metrics`에 추가한다.
  - 실패 Top-N 티커와 stage별 오류 건수를 실행 요약에 포함한다.
4. 리포트 노출
  - `run_collection_report.py` HTML에 실패 요약 카드(총 실패건, 소스별 실패율, 상위 오류 유형)를 추가한다.

### 검증 기준
- 장애 유도 테스트(의도적 잘못된 티커/네트워크 실패)에서 전체 잡이 중단되지 않고 완료된다.
- 로그에서 최소 1건 이상의 실패가 구조화 필드와 함께 확인된다.
- 리포트에 소스별 성공/실패 지표가 노출된다.
