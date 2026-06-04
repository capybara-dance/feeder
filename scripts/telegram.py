import os
from typing import Optional

import requests
from dotenv import load_dotenv


class TelegramSender:
    """Telegram messaging wrapper with optional strict error handling.

    기존 `core.telegram.TelegramSender` 를 참고하여 구현하되,
    메시지 전송 실패 시 선택적으로 예외를 발생시킬 수 있도록 확장했습니다.
    """

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        load_dotenv()
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID_TEST")

    def _ensure_credentials(self) -> bool:
        if not self.bot_token or not self.chat_id:
            print("[WARN] Missing bot token or chat id. Skip send.")
            return False
        return True

    def send_message(
        self,
        text: str,
        *,
        parse_mode: Optional[str] = "Markdown",
        raise_on_error: bool = False,
    ) -> bool:
        """텔레그램으로 텍스트 메시지를 전송합니다.

        Returns:
            성공 시 True, 실패 시 False
        """
        if not self._ensure_credentials():
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = {"chat_id": self.chat_id, "text": text}
        if parse_mode:
            data["parse_mode"] = parse_mode

        try:
            response = requests.post(url, data=data, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            response_text = ""
            if getattr(exc, "response", None) is not None:
                response_text = f" | response: {exc.response.text}"
            print(f"[ERROR] Failed to send message: {exc}{response_text}")
            if raise_on_error:
                raise RuntimeError("Failed to send Telegram message") from exc
            return False

        return True

    def send_photo(
        self,
        file_path: str,
        *,
        caption: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> bool:
        """텔레그램으로 사진을 전송합니다.

        기존 `core.telegram.TelegramSender.send_photo` 를 기반으로,
        에러 처리 방식을 `send_message` 와 통일했습니다.
        """
        if not self._ensure_credentials():
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        try:
            with open(file_path, "rb") as f:
                files = {"photo": f}
                data = {"chat_id": self.chat_id}
                if caption:
                    data["caption"] = caption
                response = requests.post(url, data=data, files=files, timeout=30)
                response.raise_for_status()
        except (OSError, requests.RequestException) as exc:
            response_text = ""
            if isinstance(exc, requests.RequestException) and getattr(exc, "response", None) is not None:
                response_text = f" | response: {exc.response.text}"
            print(f"[ERROR] Failed to send photo: {exc}{response_text}")
            if raise_on_error:
                raise RuntimeError("Failed to send Telegram photo") from exc
            return False

        return True

    def send_document(
        self,
        file_path: str,
        *,
        caption: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> bool:
        """텔레그램으로 문서를 전송합니다.

        Returns:
            성공 시 True, 실패 시 False
        """
        if not self._ensure_credentials():
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
        try:
            with open(file_path, "rb") as f:
                files = {"document": f}
                data = {"chat_id": self.chat_id}
                if caption:
                    data["caption"] = caption
                response = requests.post(url, data=data, files=files, timeout=30)
                response.raise_for_status()
        except (OSError, requests.RequestException) as exc:
            response_text = ""
            if isinstance(exc, requests.RequestException) and getattr(exc, "response", None) is not None:
                response_text = f" | response: {exc.response.text}"
            print(f"[ERROR] Failed to send document: {exc}{response_text}")
            if raise_on_error:
                raise RuntimeError("Failed to send Telegram document") from exc
            return False

        return True
