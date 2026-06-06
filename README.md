# Feeder

## 설치

프로젝트 루트 가상환경 기준으로 아래 명령을 먼저 실행합니다.

```bash
pip install -r requirements.txt
```

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

## 수집부 테스트 리포트(HTML) 생성 및 텔레그램 전송

수집부 동작을 테스트하고 결과/샘플 데이터를 HTML 리포트로 만든 뒤 텔레그램으로 전송할 수 있습니다.

- 실행 스크립트: `scripts/run_collection_report.py`
- 기본 출력 파일: `reports/collection_test_report.html`

```bash
/workspaces/feeder/.venv/bin/python scripts/run_collection_report.py \
	--test-limit 5 \
	--max-workers 1 \
	--output-html reports/collection_test_report.html
```

옵션:
- `--no-send`: HTML 생성만 수행하고 텔레그램 전송은 생략
- `--start-date`, `--end-date`: 수집 기간 지정
- `--market`: KOSPI/KOSDAQ/ETF 등 시장 필터

리포트에는 시총 품질 지표가 함께 포함됩니다.
- `market_cap_missing_before`
- `market_cap_missing_after_enrichment`
- `market_cap_zero_final`

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
