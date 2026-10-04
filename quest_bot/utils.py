"""Shared formatting and authorization helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputRichMessage

from .database import Database
from .localization import tr

LOCAL_TZ = ZoneInfo("Asia/Tashkent")


def parse_local_datetime(value: str) -> str | None:
    """Parse a Tashkent-local timestamp and return an aware UTC ISO string."""
    try:
        local_value = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(
            tzinfo=LOCAL_TZ
        )
    except ValueError:
        return None
    return local_value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def format_datetime(value: str | None, language: str) -> str:
    if not value:
        return "—"
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(value)


def parse_duration_input(
    raw: str, start_at: str | None
) -> tuple[int | None, str | None]:
    """Read a duration message as minutes, or as a Tashkent-local end time.

    Returns ``(duration_seconds, error_key)`` where ``0`` means no overall
    limit at all.
    """
    raw = (raw or "").strip()
    if raw.isdigit():
        minutes = int(raw)
        if not 0 <= minutes <= 525600:
            return None, "invalid_number"
        return minutes * 60, None
    if not start_at:
        return None, "invalid_number"
    end_at = parse_local_datetime(raw)
    if not end_at:
        return None, "invalid_number"
    try:
        start = datetime.fromisoformat(str(start_at))
        end = datetime.fromisoformat(end_at)
    except (TypeError, ValueError):
        return None, "invalid_number"
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end <= start:
        return None, "invalid_quest_end"
    minutes = int((end - start).total_seconds() // 60)
    if minutes > 525600:
        return None, "invalid_number"
    return minutes * 60, None


def quest_duration_label(language: str, duration_seconds: int) -> str:
    """Human-readable overall time, ``0`` meaning unlimited."""
    if int(duration_seconds or 0) <= 0:
        return tr(language, "quest_duration_unlimited")
    return f"{int(duration_seconds) // 60} min"


def quest_end_at(quest: dict) -> str | None:
    """Return the ISO timestamp at which a quest's overall time runs out."""
    duration = int(quest.get("duration_seconds") or 0)
    start_at = quest.get("start_at")
    if not duration or not start_at:
        return None
    try:
        start = datetime.fromisoformat(str(start_at))
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return (start + timedelta(seconds=duration)).isoformat()


RANK_MEDALS = ("🥇", "🥈", "🥉", "🏅", "🎖")


def rank_label(rank: int, medals: bool = True) -> str:
    """Prefix the first five ranks with their medal, e.g. ``🥇 1``.

    Every other rank stays a plain number so long lists remain readable. A
    participant who has not solved a single stage yet earns no medal, so the
    caller passes ``medals=False`` for them; the rank number itself is kept.
    """
    if medals and 1 <= rank <= len(RANK_MEDALS):
        return f"{RANK_MEDALS[rank - 1]} {rank}"
    return str(rank)


def display_name(
    full_name: str | None, username: str | None, user_id: int | None = None
) -> str:
    if full_name:
        return full_name
    if username:
        return f"@{username}"
    return str(user_id or "Unknown")


async def ensure_private_callback(callback: CallbackQuery, db: Database) -> bool:
    if callback.message and callback.message.chat.type == "private":
        return True
    language = await db.get_language(callback.from_user.id)
    await callback.answer(tr(language, "open_private_chat"), show_alert=True)
    return False


async def can_manage_quest(db: Database, user_id: int, quest: dict) -> bool:
    role = await db.get_role(user_id)
    return role == "superadmin" or (
        role == "admin" and int(quest["owner_id"]) == user_id
    )


async def safe_edit(
    callback: CallbackQuery,
    text: str | InputRichMessage,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Edit a callback message with plain text or structured Telegram rich content."""
    if not callback.message:
        return False
    kwargs = (
        {"rich_message": text} if isinstance(text, InputRichMessage) else {"text": text}
    )
    try:
        await callback.message.edit_text(**kwargs, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
        return False


async def send_or_edit(
    callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    if callback.message:
        await safe_edit(callback, text, reply_markup)
