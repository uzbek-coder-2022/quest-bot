"""Log updates and report unhandled user-facing failures to superadmins."""

from __future__ import annotations

import logging
import time
import traceback
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import ErrorEvent, TelegramObject, Update
from aiogram.types.update import UpdateTypeLookupError

from .config import Settings
from .database import Database
from .localization import tr
from .logging_setup import redact_sensitive_text, sensitive_values

logger = logging.getLogger(__name__)
MAX_TELEGRAM_REPORT_LENGTH = 3_700
ERROR_ALERT_COOLDOWN_SECONDS = 60
_error_alert_state: dict[str, tuple[float, int]] = {}


def update_context(update: Update) -> dict[str, Any]:
    """Extract safe identifiers without storing or forwarding user message content."""
    try:
        update_type = str(update.event_type)
    except UpdateTypeLookupError:
        update_type = "unknown"

    try:
        event = update.event
    except UpdateTypeLookupError:
        event = None

    user = getattr(event, "from_user", None)
    chat = getattr(event, "chat", None)
    if chat is None:
        message = getattr(event, "message", None)
        chat = getattr(message, "chat", None)
    return {
        "update_id": getattr(update, "update_id", None),
        "update_type": update_type,
        "user_id": getattr(user, "id", None),
        "chat_id": getattr(chat, "id", None),
        "chat_type": getattr(chat, "type", None),
    }


class UpdateLoggingMiddleware(BaseMiddleware):
    """Write an identifier-only log record for every incoming Telegram update."""

    async def __call__(
        self, handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        update = data.get("event_update")
        if isinstance(update, Update):
            context = update_context(update)
            logger.info(
                "Processing Telegram update: update_id=%s type=%s user_id=%s chat_id=%s",
                context["update_id"],
                context["update_type"],
                context["user_id"],
                context["chat_id"],
            )
            database = data.get("db")
            try:
                update_event = update.event
            except UpdateTypeLookupError:
                update_event = None
            user = getattr(update_event, "from_user", None)
            if context["chat_type"] == "private" and user and database:
                ensure_user = getattr(database, "ensure_user", None)
                if ensure_user:
                    full_name = " ".join(
                        part for part in (user.first_name, user.last_name) if part
                    )
                    await ensure_user(user.id, user.username, full_name)
        return await handler(event, data)


def build_error_report(
    exception: Exception,
    settings: Settings,
    context: dict[str, Any] | None = None,
    source: str = "Unhandled application error",
) -> str:
    """Build a compact, secret-redacted report without including user message text."""
    details = context or {}
    exception_traceback = "".join(
        traceback.format_exception(type(exception), exception, exception.__traceback__)
    )
    secrets = sensitive_values(settings.bot_token, settings.database_dsn)
    exception_traceback = redact_sensitive_text(exception_traceback, secrets)
    error_message = redact_sensitive_text(str(exception), secrets)
    if len(error_message) > 500:
        error_message = error_message[:497] + "..."
    header = (
        "Quest Bot error report\n"
        f"Source: {source}\n"
        f"Time (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"Update ID: {details.get('update_id', 'n/a')}\n"
        f"Update type: {details.get('update_type', 'n/a')}\n"
        f"User ID: {details.get('user_id', 'n/a')}\n"
        f"Chat ID: {details.get('chat_id', 'n/a')}\n"
        f"Error: {type(exception).__name__}: {error_message}\n"
        "Traceback:\n"
    )
    available_traceback_length = MAX_TELEGRAM_REPORT_LENGTH - len(header) - 40
    if len(exception_traceback) > available_traceback_length:
        exception_traceback = (
            "[Earlier traceback lines omitted]\n"
            + exception_traceback[-available_traceback_length:]
        )
    return f"{header}{exception_traceback}"[:MAX_TELEGRAM_REPORT_LENGTH]


def error_alert_key(
    exception: Exception,
    source: str,
    update_type: str = "",
) -> str:
    """Build a stable fingerprint that does not retain message text or error values."""
    frames = traceback.extract_tb(exception.__traceback__)
    location = "unknown"
    if frames:
        frame = frames[-1]
        location = f"{frame.filename}:{frame.name}:{frame.lineno}"
    return f"{source}:{update_type}:{type(exception).__name__}:{location}"


def _error_alert_decision(dedupe_key: str | None) -> tuple[bool, int]:
    """Rate-limit identical alerts while keeping every occurrence in the log files."""
    if not dedupe_key:
        return True, 0
    now = time.monotonic()
    previous = _error_alert_state.get(dedupe_key)
    if previous and now - previous[0] < ERROR_ALERT_COOLDOWN_SECONDS:
        _error_alert_state[dedupe_key] = (previous[0], previous[1] + 1)
        return False, previous[1] + 1
    suppressed = previous[1] if previous else 0
    _error_alert_state[dedupe_key] = (now, 0)
    return True, suppressed


async def notify_superadmins_of_error(
    bot: Bot,
    settings: Settings,
    report: str,
    dedupe_key: str | None = None,
) -> None:
    """Deliver an application error report to every configured superadmin."""
    should_send, suppressed_count = _error_alert_decision(dedupe_key)
    if not should_send:
        logger.info(
            "Suppressed repeated superadmin error alert; see log_err for each occurrence"
        )
        return
    if suppressed_count:
        report += (
            f"\nRepeated identical errors suppressed during the previous "
            f"{ERROR_ALERT_COOLDOWN_SECONDS} seconds: {suppressed_count}."
        )
    for user_id in settings.superadmin_ids:
        try:
            await bot.send_message(user_id, report)
        except Exception:
            logger.exception("Could not send an error report to superadmin %s", user_id)


async def handle_update_error(
    event: ErrorEvent,
    bot: Bot,
    db: Database,
    settings: Settings,
) -> bool:
    """Log an unhandled update failure, inform the user, and alert superadmins."""
    context = update_context(event.update)
    exception = event.exception
    logger.error(
        "Unhandled update error: update_id=%s type=%s user_id=%s chat_id=%s",
        context["update_id"],
        context["update_type"],
        context["user_id"],
        context["chat_id"],
        exc_info=(type(exception), exception, exception.__traceback__),
    )

    user_id = context["user_id"]
    if user_id and context["chat_type"] == "private":
        try:
            language = await db.get_language(user_id)
            await bot.send_message(user_id, tr(language, "error_generic"))
        except Exception:
            logger.exception(
                "Could not send a generic error notice to user %s", user_id
            )

    report = build_error_report(exception, settings, context, "Telegram update handler")
    dedupe_key = error_alert_key(
        exception, "Telegram update handler", str(context["update_type"])
    )
    await notify_superadmins_of_error(bot, settings, report, dedupe_key)
    return True
