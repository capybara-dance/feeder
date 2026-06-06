from __future__ import annotations

import json
import mimetypes
import os
import uuid
import urllib.error
import urllib.request
from urllib.parse import urlencode
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _load_dotenv(dotenv_path: str = ".env") -> None:
    """Load simple KEY=VALUE pairs from .env into process env if absent."""
    p = Path(dotenv_path)
    if not p.exists():
        return

    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = raw.strip().strip('"').strip("'")
        os.environ[key] = value


@dataclass(frozen=True)
class TelegramSender:
    """Telegram API sender for text, images, and generic files."""

    bot_token: str | None = None
    chat_id: str | None = None
    dotenv_path: str = ".env"

    def __post_init__(self) -> None:
        _load_dotenv(self.dotenv_path)
        token = self.bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        chat = self.chat_id or os.getenv("TELEGRAM_CHAT_ID_TEST")
        object.__setattr__(self, "bot_token", token)
        object.__setattr__(self, "chat_id", chat)

    def _ensure_credentials(self) -> None:
        if not self.bot_token or not self.chat_id:
            raise ValueError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID_TEST in environment")

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    def _post_form(self, method: str, fields: dict[str, str]) -> dict[str, Any]:
        body = urlencode(fields).encode("utf-8")
        req = urllib.request.Request(
            self._api_url(method),
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read().decode("utf-8")
                return json.loads(payload)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram HTTP error ({exc.code}): {detail}") from exc

    def _post_multipart(self, method: str, fields: dict[str, str], file_field: str, file_path: str) -> dict[str, Any]:
        file_name = Path(file_path).name
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        boundary = f"----capybara-{uuid.uuid4().hex}"

        parts: list[bytes] = []
        for key, value in fields.items():
            parts.extend(
                [
                    f"--{boundary}\r\n".encode("utf-8"),
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )

        data = Path(file_path).read_bytes()
        parts.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{file_field}"; '
                    f'filename="{file_name}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
                data,
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        body = b"".join(parts)

        req = urllib.request.Request(
            self._api_url(method),
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read().decode("utf-8")
                return json.loads(payload)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram HTTP error ({exc.code}): {detail}") from exc

    def send_text(self, text: str, parse_mode: str | None = None) -> dict[str, Any]:
        self._ensure_credentials()
        fields = {"chat_id": str(self.chat_id), "text": text}
        if parse_mode:
            fields["parse_mode"] = parse_mode
        return self._post_form("sendMessage", fields)

    def send_image(self, image_path: str, caption: str | None = None) -> dict[str, Any]:
        self._ensure_credentials()
        fields = {"chat_id": str(self.chat_id)}
        if caption:
            fields["caption"] = caption
        return self._post_multipart("sendPhoto", fields, "photo", image_path)

    def send_document(self, file_path: str, caption: str | None = None) -> dict[str, Any]:
        self._ensure_credentials()
        fields = {"chat_id": str(self.chat_id)}
        if caption:
            fields["caption"] = caption
        return self._post_multipart("sendDocument", fields, "document", file_path)

    def send_html_file(self, html_path: str, caption: str | None = None) -> dict[str, Any]:
        """Send an HTML file as Telegram document."""
        return self.send_document(html_path, caption=caption)
