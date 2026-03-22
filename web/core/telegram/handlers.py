from django.utils import timezone

from core.models import TelegramUpdateLog

from .api import TelegramAPIError, answer_callback_query, edit_message_text, get_updates, send_message
from .services import (
    build_home_response,
    build_orders_response,
    build_tasks_response,
    get_profile_by_chat_id,
    link_profile_to_chat,
    unlink_profile_by_chat,
)


def _extract_sender(update):
    payload = update.get("message") or update.get("callback_query", {}).get("message") or {}
    sender = update.get("message", {}).get("from") or update.get("callback_query", {}).get("from") or {}
    chat = payload.get("chat") or {}
    return {
        "chat_id": str(chat.get("id") or ""),
        "username": sender.get("username", ""),
    }


def _message_type(update):
    if "callback_query" in update:
        return "callback_query"
    if "message" in update:
        return "message"
    return "unknown"


def _render_browser(profile, target, scope, page):
    if target == "orders":
        return build_orders_response(profile, scope=scope, page_number=page)
    return build_tasks_response(profile, scope=scope, page_number=page)


def _send_home(chat_id, profile):
    text, reply_markup = build_home_response(profile)
    send_message(chat_id, text, reply_markup=reply_markup)


def _handle_link_command(chat_id, username, raw_code):
    profile = link_profile_to_chat(raw_code, chat_id, username=username)
    if profile:
        _send_home(chat_id, profile)
        return "processed", ""
    send_message(chat_id, "Link code not found. Open your account in CRM and use the current Telegram code.")
    return "ignored", "Invalid Telegram link code."


def _handle_message(update):
    message = update.get("message") or {}
    text = (message.get("text") or "").strip()
    if not text:
        return "ignored", "Message does not contain text."

    sender = _extract_sender(update)
    chat_id = sender["chat_id"]
    username = sender["username"]
    command, _, arg = text.partition(" ")
    command = command.split("@", 1)[0].lower()
    arg = arg.strip()

    if command == "/start":
        if arg:
            return _handle_link_command(chat_id, username, arg)
        profile = get_profile_by_chat_id(chat_id)
        if profile:
            _send_home(chat_id, profile)
        else:
            send_message(chat_id, "Use /link CODE to connect this chat with your CRM account.")
        return "processed", ""

    if command == "/link":
        return _handle_link_command(chat_id, username, arg)

    if command == "/unlink":
        profile = unlink_profile_by_chat(chat_id)
        if profile:
            send_message(chat_id, "Telegram chat disconnected from CRM.")
            return "processed", ""
        send_message(chat_id, "This chat is not linked to a CRM account.")
        return "ignored", "Chat is not linked."

    profile = get_profile_by_chat_id(chat_id)
    if not profile:
        send_message(chat_id, "This chat is not linked. Use /link CODE from your CRM account.")
        return "ignored", "Chat is not linked."

    if command == "/tasks":
        text, reply_markup = build_tasks_response(profile, scope="open", page_number=1)
        send_message(chat_id, text, reply_markup=reply_markup)
        return "processed", ""

    if command == "/orders":
        text, reply_markup = build_orders_response(profile, scope="active", page_number=1)
        send_message(chat_id, text, reply_markup=reply_markup)
        return "processed", ""

    _send_home(chat_id, profile)
    return "ignored", f"Unsupported command: {command}"


def _handle_callback(update):
    callback = update.get("callback_query") or {}
    data = (callback.get("data") or "").strip()
    sender = _extract_sender(update)
    chat_id = sender["chat_id"]
    message = callback.get("message") or {}
    profile = get_profile_by_chat_id(chat_id)
    if not profile:
        answer_callback_query(callback.get("id"), text="Chat is not linked.")
        return "ignored", "Chat is not linked."

    if data == "home":
        text, reply_markup = build_home_response(profile)
    else:
        parts = data.split(":")
        if len(parts) != 3 or parts[0] not in {"tasks", "orders"}:
            answer_callback_query(callback.get("id"), text="Unknown action.")
            return "ignored", "Unknown callback payload."
        target, scope, page_raw = parts
        page = int(page_raw) if page_raw.isdigit() else 1
        text, reply_markup = _render_browser(profile, target, scope, page)

    try:
        edit_message_text(chat_id, message.get("message_id"), text, reply_markup=reply_markup)
    except TelegramAPIError:
        send_message(chat_id, text, reply_markup=reply_markup)
    answer_callback_query(callback.get("id"))
    return "processed", ""


def process_update(update):
    update_id = update.get("update_id")
    if update_id is None:
        return False

    sender = _extract_sender(update)
    log, created = TelegramUpdateLog.objects.get_or_create(
        update_id=update_id,
        defaults={
            "chat_id": sender["chat_id"],
            "username": sender["username"],
            "update_type": _message_type(update),
            "payload": update,
            "status": TelegramUpdateLog.Status.PROCESSED,
            "processed_at": timezone.now(),
        },
    )
    if not created and log.status in {TelegramUpdateLog.Status.PROCESSED, TelegramUpdateLog.Status.IGNORED}:
        return False

    try:
        if "callback_query" in update:
            status, error_message = _handle_callback(update)
        elif "message" in update:
            status, error_message = _handle_message(update)
        else:
            status, error_message = "ignored", "Unsupported update type."
        log.chat_id = sender["chat_id"]
        log.username = sender["username"]
        log.update_type = _message_type(update)
        log.payload = update
        log.status = (
            TelegramUpdateLog.Status.PROCESSED
            if status == "processed"
            else TelegramUpdateLog.Status.IGNORED
        )
        log.error_message = error_message
        log.processed_at = timezone.now()
        log.save(
            update_fields=[
                "chat_id",
                "username",
                "update_type",
                "payload",
                "status",
                "error_message",
                "processed_at",
            ]
        )
    except Exception as exc:
        log.chat_id = sender["chat_id"]
        log.username = sender["username"]
        log.update_type = _message_type(update)
        log.payload = update
        log.status = TelegramUpdateLog.Status.FAILED
        log.error_message = str(exc)
        log.processed_at = timezone.now()
        log.save(
            update_fields=[
                "chat_id",
                "username",
                "update_type",
                "payload",
                "status",
                "error_message",
                "processed_at",
            ]
        )
        raise
    return True


def pull_updates_and_process(*, offset=None, limit=100, timeout=0):
    updates = get_updates(offset=offset, limit=limit, timeout=timeout)
    next_offset = offset
    processed = 0
    for update in updates:
        process_update(update)
        processed += 1
        next_offset = max(next_offset or 0, update.get("update_id", 0) + 1)
    return {
        "processed": processed,
        "next_offset": next_offset,
    }
