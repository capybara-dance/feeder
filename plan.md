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
  - `MARKET_CODE`: KOSPI=1, KOSDAQ=2, KONEX=3 (그 외 정책 필요)
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
