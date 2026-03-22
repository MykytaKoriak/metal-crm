import json
from urllib import error, request

from django.conf import settings


class TelegramAPIError(Exception):
    """Raised when the Telegram Bot API returns an error."""


def _bot_api_url(method: str) -> str:
    token = (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not token:
        raise TelegramAPIError("TELEGRAM_BOT_TOKEN is not configured.")
    return f"https://api.telegram.org/bot{token}/{method}"


def telegram_api_request(method: str, payload=None):
    body = json.dumps(payload or {}).encode("utf-8")
    http_request = request.Request(
        _bot_api_url(method),
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise TelegramAPIError(details or f"Telegram API HTTP error: {exc.code}") from exc
    except error.URLError as exc:
        raise TelegramAPIError(str(exc.reason)) from exc

    data = json.loads(raw or "{}")
    if not data.get("ok"):
        raise TelegramAPIError(data.get("description") or "Telegram API request failed.")
    return data.get("result")


def send_message(chat_id, text, *, reply_markup=None, disable_web_page_preview=True):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_api_request("sendMessage", payload)


def edit_message_text(chat_id, message_id, text, *, reply_markup=None, disable_web_page_preview=True):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_api_request("editMessageText", payload)


def answer_callback_query(callback_query_id, *, text=None):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return telegram_api_request("answerCallbackQuery", payload)


def get_updates(*, offset=None, limit=100, timeout=0):
    payload = {
        "limit": limit,
        "timeout": timeout,
        "allowed_updates": ["message", "callback_query"],
    }
    if offset is not None:
        payload["offset"] = offset
    return telegram_api_request("getUpdates", payload)
