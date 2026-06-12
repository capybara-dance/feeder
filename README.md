# Feeder

## 설치

프로젝트 루트 가상환경 기준으로 아래 명령을 먼저 실행합니다.

```bash
pip install -r requirements.txt
```

## Streamlit 조회 앱

프로젝트 루트에서 Oracle DB 데이터를 조회하는 Streamlit 앱을 실행할 수 있습니다.

- 앱 파일: `streamlit_app.py`
- 조회 대상 테이블: `STOCK_MASTER`, `DAILY_PRICE`

필수 환경 변수:
- `OCI_DB_USER`
- `OCI_DB_PW`
- `OCI_DB_DSN`

실행:

```bash
/workspaces/feeder/.venv/bin/streamlit run streamlit_app.py
```

제공 기능:
- 티커/종목명 검색
- 티커 선택 시 TradingView 차트 표시
- 종가 기준 20일 이동평균선(MA20) 표시
- 최근 가격 데이터 표 확인

참고:
- TradingView Lightweight Charts Library로 렌더링합니다.
- 차트 데이터는 Oracle DB(`DAILY_PRICE`) 조회 결과만 사용합니다.

## 데이터 명세 문서

- 상세 데이터 항목/타입/수집 소스/구현 상태: `docs/data_dictionary.md`

샘플 레코드를 최근 실제 수집 결과로 자동 갱신하려면:

```bash
/workspaces/feeder/.venv/bin/python scripts/update_data_dictionary_samples.py \
	--test-limit 10 \
	--max-workers 1
```

## Telegram 전송 기능 사용법

이 프로젝트에는 신규 구현 텔레그램 전송 모듈이 포함되어 있습니다.

- 모듈 위치: `capybara_fetcher/notifications/telegram_sender.py`

### 1) 환경 변수 설정

프로젝트 루트 `.env`에 아래 값을 설정합니다.

```env
TELEGRAM_BOT_TOKEN=<your_bot_token>
TELEGRAM_CHAT_ID_TEST=<your_chat_id>
```

참고:
- `TELEGRAM_CHAT_ID_TEST`가 기본 전송 대상입니다.
- 생성자 인자로 `bot_token`, `chat_id`를 직접 넘기면 `.env` 값을 덮어쓸 수 있습니다.

### 2) 텍스트 전송

```python
from capybara_fetcher.notifications import TelegramSender

sender = TelegramSender()
sender.send_text("hello capybara")
```

HTML/Markdown 스타일 메시지는 `parse_mode`를 지정해 전송할 수 있습니다.

```python
sender.send_text("<b>Capybara</b> report", parse_mode="HTML")
```

### 3) 이미지 전송

```python
from capybara_fetcher.notifications import TelegramSender

sender = TelegramSender()
sender.send_image("./sample.png", caption="price snapshot")
```

### 4) 문서/HTML 파일 전송

일반 문서 파일은 `send_document`, HTML 파일은 `send_html_file`로 전송합니다.

```python
from capybara_fetcher.notifications import TelegramSender

sender = TelegramSender()
sender.send_document("./report.txt", caption="daily text report")
sender.send_html_file("./report.html", caption="daily html report")
```

### 5) 빠른 동작 확인

아래 명령으로 텍스트 메시지 `hello capybara`를 전송할 수 있습니다.

```bash
/workspaces/feeder/.venv/bin/python -c "from capybara_fetcher.notifications import TelegramSender; print(TelegramSender().send_text('hello capybara'))"
```

정상 전송 시 응답 JSON에서 `ok: true`를 확인할 수 있습니다.

## DB 샘플 리포트(HTML) 생성 및 텔레그램 전송

Oracle DB의 각 테이블 일부 데이터를 조회해 HTML 리포트를 만든 뒤 텔레그램으로 전송할 수 있습니다.

- 실행 스크립트: `scripts/run_collection_report.py`
- 기본 출력 파일: `reports/collection_test_report.html`

```bash
/workspaces/feeder/.venv/bin/python scripts/run_collection_report.py \
	--sample-rows 15 \
	--output-html reports/collection_test_report.html
```

옵션:
- `--no-send`: HTML 생성만 수행하고 텔레그램 전송은 생략
- `--sample-rows`: 각 테이블 샘플 행 수

필수 DB 환경 변수:
- `OCI_DB_USER`
- `OCI_DB_PW`
- `OCI_DB_DSN`

선택(ATP Wallet):
- `OCI_WALLET`
- `OCI_WALLET_PW`

리포트 대상 테이블:
- `STOCK_INDUSTRY`
- `STOCK_MASTER`
- `DAILY_PRICE`
- `STOCK_DIVIDEND`
- `ETF_COMPONENT`

배당 수집:
- 내부 `yfinance` provider를 통해 티커별 배당 내역을 조회해 `dividend_df`를 생성합니다.
- Yahoo 심볼은 한국 종목 기준 `.KS` 우선, 필요 시 `.KQ` fallback으로 조회합니다.
- 현재 저장 컬럼 매핑: `TICKER`, `EX_DIVIDEND_DATE`, `DIVIDEND_PER_SHARE`, `RECORD_DATE`, `PAYMENT_DATE`, `DIVIDEND_TYPE`.

## GitHub Actions 실행

수집 리포트 실행 워크플로는 아래 파일에 추가되어 있습니다.

- `.github/workflows/run_collection_report.yml`

동작 방식:
- 수동 실행: GitHub Actions에서 `Run Collection Report`를 `workflow_dispatch`로 실행
- 자동 실행: `scripts/run_collection_report.py` 파일에 커밋(push) 발생 시 자동 실행

환경 변수 설정:
- `.env`의 각 키를 동일한 이름의 Repository Secret으로 각각 저장
- 워크플로에서 각 Secret을 job `env`로 직접 매핑해 런타임 환경 변수로 사용

참고:
- 텔레그램 전송을 사용하려면 `.env`에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID_TEST` 등이 포함되어 있어야 합니다.
- 실행 결과 HTML은 artifact `collection-test-report`로 업로드됩니다.

## Oracle DB 적재 실행

현재 구현 완료된 수집 데이터(`STOCK_INDUSTRY`, `STOCK_MASTER`, `DAILY_PRICE`, `STOCK_DIVIDEND`)를 OracleDB로 upsert할 수 있습니다.

- 실행 스크립트: `scripts/sync_oracle.py`

필수 환경 변수:
- `OCI_DB_USER`
- `OCI_DB_PW`
- `OCI_DB_DSN`

참고:
- `scripts/sync_oracle.py`는 실행 초기에 repo 루트의 `.env`를 먼저 읽어 `KRX_ID`, `KRX_PW` 같은 값도 import 이전부터 반영합니다.
- 이미 프로세스 환경변수에 값이 있으면 `.env` 값으로 덮어쓰지 않습니다.
- `pykrx`는 지연 import되므로, KRX 로그인 응답이 깨져도 스크립트 시작 자체가 실패하지 않고 `CompositeProvider`가 FDR로 폴백합니다.
- `pykrx` import가 한 번 실패하면 같은 실행 안에서는 다시 로그인 시도를 반복하지 않고 바로 FDR로 넘어갑니다.
- `pykrx`가 실제 OHLCV 조회에서 한 번 실패하면 같은 실행 안에서는 추가 로그인 시도를 하지 않고 FDR로 고정됩니다.
- `FDR` provider는 KRX 실패 시 더 이상 NAVER로 자동 전환하지 않습니다. KRX가 막히면 실패를 바로 드러냅니다.
- 알파벳이 포함된 티커는 수집 대상에서 제거하지 않으며, pykrx는 6자리 숫자 티커에만 사용합니다.

선택 환경 변수(ATP/Wallet 연결 시):
- `OCI_WALLET` (base64 인코딩된 wallet zip)
- `OCI_WALLET_PW`

실행 예시:

```bash
/workspaces/feeder/.venv/bin/python scripts/sync_oracle.py \
	--mode full-10y \
	--test-limit 100 \
	--max-workers 8 \
	--batch-size 2000

GitHub Release 기반 적재(초기 10년치 권장):

```bash
/workspaces/feeder/.venv/bin/python scripts/sync_oracle.py \
	--source release \
	--mode full-10y \
	--release-repo capybara-dance/capybara_fetcher \
	--release-tag data-YYYYMMDD-HHMM \
	--batch-size 5000
```

- `--release-tag`를 생략하면 최신 릴리즈(`latest`)를 사용합니다.
- 릴리즈 자산에서 아래 파일을 읽어 DB upsert에 사용합니다.
  - `korea_universe_feature_frame.parquet`
  - `krx_stock_master.parquet`
- `source=release`는 현재 `mode=full-10y` 또는 `mode=range`만 지원합니다(`daily` 미지원).

참고: 운영 전략은 먼저 `source=release`로 초기 적재를 수행하고, 이후 `source=collect + mode=daily` 경로로 일일 upsert를 이어가는 방식입니다.
```

기본 일일 모드:
- `--mode daily` (기본)
- 기본적으로 KST 기준 당일 데이터를 수집/업서트
- DB `DAILY_PRICE`를 조회해 `오늘-10일` 구간의 영업일 누락 데이터가 있으면 해당 날짜도 함께 재수집/업데이트

범위 지정 모드:

```bash
/workspaces/feeder/.venv/bin/python scripts/sync_oracle.py \
	--mode range \
	--start-date 2024-01-01 \
	--end-date 2024-01-31
```

드라이런(수집만 수행, DB 반영 없음):

```bash
/workspaces/feeder/.venv/bin/python scripts/sync_oracle.py --dry-run
```

진행 상황 확인:
- 기본값으로 release 적재 시 배치 진행률을 표시합니다.
- `tqdm`이 설치되어 있으면 progress bar를, 없으면 배치 로그를 출력합니다.
- progress bar를 끄려면 `--no-progress` 옵션을 사용합니다.

```bash
/workspaces/feeder/.venv/bin/python scripts/sync_oracle.py \
	--source release \
	--mode full-10y \
	--release-repo capybara-dance/capybara_fetcher \
	--no-send-report
```

UNDO 관련 오류(`ORA-30036`) 대응:
- 대량 upsert 시 트랜잭션이 길어지면 UNDO tablespace 부족이 발생할 수 있습니다.
- `OCI_COMMIT_EVERY_BATCHES` 환경변수로 `executemany` 커밋 주기를 조절할 수 있습니다(기본값 `1`, 즉 배치마다 커밋).
- release full-10y 전용 workflow에서는 `commit_every_batches` 입력값으로 동일 설정을 제어할 수 있습니다.

배당 수집 비활성화(속도 최적화):

```bash
/workspaces/feeder/.venv/bin/python scripts/sync_oracle.py --skip-dividends
```

- `--skip-dividends`를 사용하면 `yfinance` 배당 조회를 생략하고 `STOCK_DIVIDEND` upsert는 0건으로 수행됩니다.

### Oracle 적재 워크플로

- 파일: `.github/workflows/sync_oracle.yml`
- 트리거:
	- 스케줄: 매일 21:00 KST 자동 실행
	- 수동: `workflow_dispatch`로 실행 가능
- 수동 실행 시 `run_mode`로 `daily`, `full-10y`, `range` 선택 가능
- 수동 실행 시 `collect_dividends=false`를 선택하면 배당 수집을 끌 수 있습니다(`--skip-dividends` 전달)

### Release full-10y 전용 워크플로(분리)

- 파일: `.github/workflows/sync_oracle_release_full.yml`
- 목적: 아래 명령을 별도 워크플로로 실행

```bash
python scripts/sync_oracle.py --source release --mode full-10y --release-repo capybara-dance/capybara_fetcher
```

- 트리거: `workflow_dispatch` (수동 실행)
- 입력값:
	- `release_repo` (기본: `capybara-dance/capybara_fetcher`)
	- `release_tag` (비우면 `latest`)
	- `batch_size`
	- `no_progress`

시총 보강 순서:
1. 원천 데이터의 시총 컬럼 사용
2. pykrx 시총 조회 결과 병합
3. `CLOSE_PRICE * SharesOutstanding` 계산값으로 결측 보정
4. 마지막 fallback으로만 0 적용
5. pykrx 실패 시 `korea_investment` 마스터 기반 시총 snapshot fallback 적용

### 시총 API 이슈 및 향후 계획

- 현재 `pykrx` 시총 조회 API는 일부 종목/구간에서 간헐적으로 실패할 수 있습니다.
- 이로 인해 시총 결측이 남아 fallback(0)으로 처리되는 케이스가 존재합니다.
- 현재 상태:
	1. `CompositeProvider` 내부에 `pykrx -> korea_investment(snapshot)` fallback을 구현함
	2. 수집 테스트 리포트에서 시총 품질 지표를 확인 가능
- 향후 개선 계획:
	1. 한국투자 API의 일별 시총 조회 endpoint가 확인되면 snapshot이 아닌 시계열 값으로 대체
	2. 소스별(원천/pykrx/kis/calc/fallback) 기여 비율 지표를 리포트에 추가
