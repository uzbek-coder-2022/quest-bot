"""Shared formatting and authorization helpers."""

from __future__ import annotations

from datetime import datetime, timezone
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


RANK_MEDALS = ("🥇", "🥈", "🥉", "🏅", "🎖")


def rank_label(rank: int) -> str:
    """Prefix the first five ranks with their medal, e.g. ``🥇 1``.

    Every other rank stays a plain number so long lists remain readable.
    """
    if 1 <= rank <= len(RANK_MEDALS):
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
