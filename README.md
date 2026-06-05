# Feeder

## Telegram 전송 기능 사용법

이 프로젝트에는 신규 구현 텔레그램 전송 모듈이 포함되어 있습니다.

- 모듈 위치: `capybara_fetcher/notifications/telegram_sender.py`
- 테스트 스크립트: `scripts/send_hello_telegram.py`

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
/workspaces/feeder/.venv/bin/python scripts/send_hello_telegram.py
```

정상 전송 시 콘솔에 `Telegram send succeeded`가 출력됩니다.
