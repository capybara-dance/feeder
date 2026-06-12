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
- `docs/data_dictionary.md`를 OracleDB 기준 데이터 계약 문서로 보강했다(Oracle 사용 명시, 키/제약/NULL 규칙, 값 매핑 규칙, 시간/단위/정밀도 규칙, 실행/검증 예제 추가).
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
- `scripts/sync_oracle.py`가 repo 루트 `.env`를 `capybara_fetcher` import 전에 먼저 읽도록 변경해, `KRX_ID`/`KRX_PW` 같은 pykrx 관련 환경변수가 import 시점부터 반영되게 했다.
- 조기 dotenv 로딩을 별도 경량 모듈 `scripts/dotenv_loader.py`로 분리하고, 회귀 테스트 `tests/test_sync_oracle.py`를 추가했다.
- `capybara_fetcher/providers/pykrx_provider.py`를 지연 import로 바꿔 import-time KRX 로그인 실패가 스크립트 시작을 깨지 않게 했다. 실패 시 `CompositeProvider`가 FDR로 폴백한다.
- `pykrx` import 실패를 캐시해서 같은 실행 안에서 로그인 재시도를 반복하지 않도록 했다.
- `CompositeProvider`가 pykrx OHLCV 실패를 런타임에 캐시해서, 같은 실행 안에서 반복 로그인/재시도를 하지 않도록 했다.
- `FdrProvider`의 KRX -> NAVER 자동 전환을 제거해 과도한 fallback을 줄였다.
- 알파벳 포함 티커는 제거하지 않고, pykrx는 6자리 숫자 티커에만 사용하도록 라우팅을 정리했다.
- `scripts/sync_oracle.py`에 `--skip-dividends` 옵션을 추가해 배당 수집 단계를 비활성화할 수 있게 했다.
- `.github/workflows/sync_oracle.yml` 수동 입력에 `collect_dividends`(boolean)를 추가해 Action에서 배당 수집 on/off를 제어할 수 있게 했다.
- `scripts/sync_oracle.py`와 `.github/workflows/sync_oracle.yml`의 기본 `max_workers` 값을 1에서 8로 변경했다.

## 18) 세션 핸드오프

### Completed
- `scripts/sync_oracle.py`의 `.env` 로딩 순서를 import 이전으로 이동했다.
- repo 루트 `.env`를 기준으로 읽도록 해서 실행 위치에 덜 의존하게 했다.
- dotenv 로직을 `scripts/dotenv_loader.py`로 분리하고 테스트를 추가했다.
- `pykrx_provider.py`에서 `pykrx.stock`를 지연 import하도록 바꿔 import-time 로그인 실패를 방지했다.
- `pykrx` import 실패 캐시를 추가해 반복 로그인 시도를 막았다.
- `CompositeProvider`에 pykrx 실패 캐시를 넣어 수집 루프에서 같은 실패를 반복하지 않도록 했다.
- `FdrProvider`에서 KRX 실패 시 NAVER로 자동 전환하지 않도록 바꿨다.
- 알파벳 포함 티커를 수집 대상에서 제외하지 않도록 복구했다.

### In progress
- 없음.

### Next 3 concrete tasks
1. 필요하면 `pykrx` 로그인 실패 재현 경로를 별도 로그로 더 좁힌다.
2. `KRX_ID`/`KRX_PW` 외에 추가로 필요한 pykrx 관련 환경변수가 있는지 확인한다.
3. Oracle 적재 워크플로의 실제 운영 로그를 한 번 더 점검한다.

### Risks / blockers
- 현재 환경에는 `oracledb`가 없어 전체 스크립트 end-to-end import 실행은 여기서 직접 검증하지 못했다.
- `pykrx`가 `.env` 외에 추가 인증 상태를 요구하면 별도 조치가 필요할 수 있다.

### Commands used for verification
- `python -m py_compile capybara_fetcher/providers/pykrx_provider.py scripts/dotenv_loader.py scripts/sync_oracle.py`
- `python -m pytest -q tests/test_sync_oracle.py`

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

## 20) Oracle 조회 Streamlit 앱 추가 (2026-06-08)

### 구현 내용
- 루트에 `streamlit_app.py`를 추가했다.
- Oracle DB(`STOCK_MASTER`)에서 티커/종목명 검색 기능을 구현했다.
- 선택 티커 기준 최근 1년(`365일`) `DAILY_PRICE`를 조회해 캔들 차트로 표시한다.
- 최근 가격 데이터 30행을 표로 함께 노출한다.
- 앱 시작 시 repo 루트 `.env`를 로딩해 Oracle 접속 환경변수를 사용한다.

### 문서/의존성 반영
- `requirements.txt`에 `streamlit`, `plotly`를 추가했다.
- `README.md`에 Streamlit 앱 실행 방법과 기능 설명을 추가했다.

## 21) 세션 핸드오프 (2026-06-08)

### Completed
- `streamlit_app.py` 신규 추가 (Oracle 검색 + 1년 캔들 차트).
- `requirements.txt`에 Streamlit 시각화 의존성 추가.
- `README.md`에 실행/사용 방법 문서화.

### In progress
- 없음.

### Next 3 concrete tasks
1. Streamlit 앱에 기간 선택(3개월/6개월/1년) 옵션 추가.
2. 거래량 보조 차트(서브플롯) 추가.
3. 검색 결과 정렬 옵션(티커 우선/이름 우선) 및 페이지네이션 추가.

### Risks / blockers
- 현재 환경에서 실제 Oracle 접속 정보 미보유 시 앱 실행 검증은 제한된다.

### Commands used for verification
- `python -m py_compile streamlit_app.py`

## 22) 릴리즈 기반 Oracle 적재 경로 추가 (2026-06-10)

### 구현 내용
- `capybara_fetcher/pipeline/release_ingest.py`를 추가해 GitHub Release API에서 자산 URL을 조회하고 Parquet를 읽어 DB upsert 입력 포맷으로 변환하도록 구현했다.
- `scripts/sync_oracle.py`에 `--source release` 모드를 추가해 수집 소스를 `collect`/`release`로 선택할 수 있게 했다.
- 릴리즈 모드에서는 `korea_universe_feature_frame.parquet`, `krx_stock_master.parquet`를 사용해 `industry/master/price` 프레임을 생성하고 Oracle upsert를 수행한다.
- `source=release`에서는 아직 `mode=daily`를 지원하지 않도록 명시적으로 차단했다(향후 일일 upsert는 `source=collect + mode=daily` 경로로 확장 예정).
- HTML 리포트에 소스 타입 및 릴리즈 메타(repo/tag/name/published_at)를 노출하도록 보강했다.

### 문서/테스트 반영
- `tests/test_release_ingest.py`를 추가해 릴리즈 자산 매핑/필수 자산 검증/날짜 필터 로직을 테스트했다.
- `requirements.txt`에 Parquet 엔진 의존성 `pyarrow`를 추가했다.
- `README.md`에 릴리즈 기반 초기 적재 실행 예시와 운영 전략(초기 release 적재 후 daily upsert 전환)을 문서화했다.

## 23) 세션 핸드오프 (2026-06-10)

### Completed
- 릴리즈 기반 DB 업데이트 경로(`--source release`)를 구현했다.
- 릴리즈 자산(피처/마스터)에서 Oracle upsert 입력 스키마로 변환하는 파이프라인을 추가했다.
- 릴리즈 모드 실행 정보를 HTML 리포트에 노출했다.
- 릴리즈 ingest 단위 테스트를 추가했다.
- 문서와 의존성을 동기화했다(`README.md`, `requirements.txt`, `plan.md`).

### In progress
- 없음.

### Next 3 concrete tasks
1. `source=release`에 `daily` 동작 정의(최신 릴리즈 diff 기반/날짜 필터 기반 중 선택)와 구현.
2. 릴리즈 메타/자산 checksum 저장 테이블(또는 로그) 추가로 중복 재처리 방지.
3. GitHub Actions `sync_oracle.yml`에 릴리즈 모드 입력 옵션 추가.

### Risks / blockers
- 릴리즈 자산 스키마가 변경되면 파서가 실패할 수 있으므로 스키마 드리프트 감시가 필요하다.
- `pd.read_parquet` 실행을 위해 환경에 `pyarrow`가 반드시 설치되어야 한다.

### Commands used for verification
- `/workspaces/feeder/.venv/bin/python -m pytest -q tests/test_release_ingest.py tests/test_sync_oracle.py`

## 24) 릴리즈 full 적재 SIGTERM(143) 대응 (2026-06-10)

### 구현 내용
- `capybara_fetcher/db/repository.py`의 upsert 경로를 청크 처리로 변경했다.
- 기존에는 `DAILY_PRICE` 전체 행을 한 번에 `list[dict]`로 물질화해 메모리 피크가 컸다.
- 현재는 DataFrame을 lazy chunk(기본 50,000행)로 나눠 Oracle `executemany`를 반복 호출한다.
- 동일 방식으로 `STOCK_MASTER`, `STOCK_INDUSTRY`, `STOCK_DIVIDEND`도 chunk upsert로 통일했다.

### 효과
- 대용량 release 적재 시 upsert 시작 직후 발생하던 메모리 급증을 완화한다.
- `batch_size`는 DB roundtrip 단위, chunk는 Python 메모리 피크 단위를 제어한다.

### Commands used for verification
- `/workspaces/feeder/.venv/bin/python -m pytest -q tests/test_sync_oracle.py tests/test_release_ingest.py`

### 추가 반영 (2026-06-10)
- 릴리즈 적재가 수집 단계에서 SIGTERM(143)으로 종료되는 케이스를 줄이기 위해, 릴리즈 feature parquet 전체 로드 방식을 제거했다.
- `scripts/sync_oracle.py`의 `source=release` 경로를 스트리밍 처리로 전환해, parquet row-batch 단위로 `DAILY_PRICE` upsert를 수행하도록 변경했다.
- 임시 다운로드 파일 정리(cleanup) 로직을 추가했다.
- `ORA-02291(FK_DAILY_PRICE_TICKER)` 대응으로 release price 배치에서 `STOCK_MASTER`에 없는 티커 행을 사전 제거하도록 보강했다.
- upsert 진행 관측성을 위해 release 경로 배치 루프에 progress bar(`tqdm`) 및 주기적 로그를 추가했고, repository 청크 upsert에도 chunk 진행 로그를 추가했다.
- 기존 `sync_oracle.yml`과 분리된 release full-10y 전용 워크플로 `.github/workflows/sync_oracle_release_full.yml`을 추가했다.
- GitHub Actions에서 발생한 `ORA-30036(UNDO 부족)` 대응으로 `OracleClient.execute_many`에 주기적 커밋을 추가했고, `OCI_COMMIT_EVERY_BATCHES`(기본 1)로 커밋 주기를 조절 가능하게 했다.

## 25) Streamlit 차트 TradingView 전환 (2026-06-12)

### 구현 내용
- `streamlit_app.py`의 Plotly 캔들 차트를 TradingView 위젯 임베드 방식으로 전환했다.
- 티커를 TradingView 심볼 규칙으로 변환하는 로직을 추가했다(6자리 숫자 티커는 `KRX:{ticker}`).
- 차트 하단 데이터 표(`DAILY_PRICE` 최근 30행)는 기존과 동일하게 Oracle DB 조회 결과를 유지한다.

### 문서/의존성 반영
- `README.md` Streamlit 기능 설명을 TradingView 기준으로 갱신했다.
- `requirements.txt`에서 미사용 의존성 `plotly`를 제거했다.

### Commands used for verification
- `/workspaces/feeder/.venv/bin/python -m py_compile streamlit_app.py`

### 추가 보완 (2026-06-12)
- TradingView 차트 로드 시 일부 티커에서 심볼 팝업이 발생하는 문제를 줄이기 위해 `streamlit_app.py` 임베드 방식을 `advanced-chart` 위젯으로 교체했다.
- 티커 심볼 정규화를 강화해 6자리 숫자 추출 기반으로 `KRX:{ticker}` 매핑을 수행하도록 수정했다.
- TradingView 심볼 변환이 불가한 경우 앱에서 경고를 표시하도록 방어 로직을 추가했다.

### 추가 보완 2 (2026-06-12)
- 심볼 기반 위젯에서 발생하던 "TradingView에서만 제공되는 심볼" 팝업을 근본적으로 제거하기 위해, `streamlit_app.py`를 TradingView `lightweight-charts` 기반 렌더링으로 전환했다.
- 차트 높이를 확대해 기존보다 더 긴 세로 뷰(약 860px, Streamlit 컴포넌트 높이 880px)로 표시되게 조정했다.
- 외부 심볼 조회 대신 Oracle DB 조회 OHLCV를 직접 전달해 캔들/거래량 히스토그램을 렌더링하도록 변경했다.

### 추가 보완 3 (2026-06-12)
- 차트가 표시되지 않는 문제 대응으로 `lightweight-charts` CDN을 버전 고정(`4.2.0`)으로 변경해 API 호환성을 확보했다.
- 차트 렌더링 시 컨테이너 크기 fallback(`width/height`)을 추가하고, 실패 시 컴포넌트 내부 오류 메시지를 표시하도록 방어 로직을 추가했다.
- 세로 길이를 추가 확대해 Streamlit 컴포넌트 높이를 `920px`로 조정했다.

### 추가 보완 4 (2026-06-12)
- 사용자 요청에 따라 차트를 TradingView `lightweight-charts` 방식에서 기본 `advanced-chart` 위젯으로 롤백했다.
- 드로잉/도구 UI가 다시 보이도록 `hide_side_toolbar=false` 설정으로 복원했다.
- 세로 길이는 유지/확대 상태로 적용해 차트 영역이 짧아 보이지 않도록 `height`를 상향 유지했다.

### 추가 보완 5 (2026-06-12)
- 사용자 요청에 따라 TradingView 기본 위젯의 세로 높이를 추가로 확대했다(컨테이너 1100px, Streamlit 컴포넌트 1120px).

### 추가 보완 6 (2026-06-12)
- 세로 높이 반영 안정성과 심볼 오류 시 AAPL 폴백 문제 완화를 위해 `streamlit_app.py` 차트 렌더링을 TradingView 스크립트 임베드에서 `widgetembed` iframe 방식으로 전환했다.
- 차트 높이를 1320px(컴포넌트 1340px)로 상향해 Streamlit iframe 내부에서도 높이가 확실히 반영되도록 조정했다.
- 검색 결과 중 숫자 6자리 티커 개수를 안내해 TradingView 지원 가능 티커를 사용자가 쉽게 구분할 수 있게 했다.

### 추가 보완 7 (2026-06-12)
- 차트 세로 길이가 과도하다는 피드백에 따라 고정 높이(1320px)를 제거하고, TradingView iframe 컨테이너를 `aspect-ratio: 6 / 4` 비율로 렌더링하도록 변경했다.

### 추가 보완 8 (2026-06-12)
- 사용자 요청에 따라 Streamlit 차트를 TradingView 심볼 위젯 방식에서 `Lightweight Charts Library` 방식으로 전환했다.
- 차트 렌더링 데이터는 외부 심볼/시세를 사용하지 않고 Oracle DB(`DAILY_PRICE`) 조회 OHLCV만 사용하도록 고정했다.
- 차트 비율은 기존 요청대로 `6:4`를 유지했다.
