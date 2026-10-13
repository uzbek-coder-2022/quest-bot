"""Shared formatting and authorization helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
)
from pydantic import ValidationError

from .database import Database
from .localization import tr
from .rich_buttons import ButtonAlignment, embed_inline_keyboard

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


async def answer_with_buttons(
    message: Message,
    content: str | InputRichMessage,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    button_alignment: ButtonAlignment = "center",
    **kwargs,
) -> Message:
    """Answer with rich buttons where possible, retaining safe fallbacks."""
    original_content, original_markup = content, reply_markup
    content, reply_markup = embed_inline_keyboard(
        content, reply_markup, align=button_alignment
    )
    if isinstance(content, InputRichMessage):
        answer_rich = getattr(message, "answer_rich", None)
        if answer_rich is not None:
            return await answer_rich(content, reply_markup=reply_markup, **kwargs)
        # Lightweight Message test doubles may expose only the legacy method.
        if isinstance(original_content, str):
            return await message.answer(
                original_content, reply_markup=original_markup, **kwargs
            )
        return await message.answer_rich(
            content, reply_markup=reply_markup, **kwargs
        )
    return await message.answer(content, reply_markup=reply_markup, **kwargs)


async def answer_rich_with_buttons(
    message: Message,
    content: InputRichMessage,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    button_alignment: ButtonAlignment = "center",
    **kwargs,
) -> Message:
    """Send rich content, embedding supported keyboards as aligned rich buttons."""
    return await answer_with_buttons(
        message,
        content,
        reply_markup,
        button_alignment=button_alignment,
        **kwargs,
    )


async def send_with_buttons(
    bot: Bot,
    chat_id: int | str,
    content: str | InputRichMessage,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    button_alignment: ButtonAlignment = "center",
    **kwargs,
) -> Message:
    """Send a chat message with rich buttons where supported."""
    original_content, original_markup = content, reply_markup
    content, reply_markup = embed_inline_keyboard(
        content, reply_markup, align=button_alignment
    )
    if isinstance(content, InputRichMessage):
        send_rich_message = getattr(bot, "send_rich_message", None)
        if send_rich_message is not None:
            return await send_rich_message(
                chat_id, content, reply_markup=reply_markup, **kwargs
            )
        # Lightweight bot test doubles may expose only the legacy method.
        if isinstance(original_content, str):
            return await bot.send_message(
                chat_id,
                original_content,
                reply_markup=original_markup,
                **kwargs,
            )
        return await bot.send_rich_message(
            chat_id, content, reply_markup=reply_markup, **kwargs
        )
    return await bot.send_message(
        chat_id, content, reply_markup=reply_markup, **kwargs
    )


async def send_rich_with_buttons(
    bot: Bot,
    chat_id: int | str,
    content: InputRichMessage,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    button_alignment: ButtonAlignment = "center",
    **kwargs,
) -> Message:
    """Send rich content to a chat, keeping compatible buttons in the same message."""
    return await send_with_buttons(
        bot,
        chat_id,
        content,
        reply_markup,
        button_alignment=button_alignment,
        **kwargs,
    )


def rich_content_without_buttons(message: Message) -> InputRichMessage | None:
    """Convert received rich content back to input form without button rows."""
    rich_message = getattr(message, "rich_message", None)
    if not rich_message:
        return None
    payload = rich_message.model_dump(mode="json", exclude_none=True)
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return None
    payload["blocks"] = [
        block
        for block in blocks
        if not isinstance(block, dict) or block.get("type") != "buttons"
    ]
    if not payload["blocks"]:
        return None
    try:
        return InputRichMessage.model_validate(payload)
    except ValidationError:
        # Output-only media blocks cannot always be resubmitted as input. The
        # caller may still use the legacy editReplyMarkup fallback for old
        # non-rich messages; never discard content that cannot be reconstructed.
        return None


async def safe_edit(
    callback: CallbackQuery,
    text: str | InputRichMessage,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    button_alignment: ButtonAlignment = "center",
) -> bool:
    """Edit a message, embedding supported keyboards into rich-message content."""
    if not callback.message:
        return False
    text, reply_markup = embed_inline_keyboard(
        text, reply_markup, align=button_alignment
    )
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


async def safe_edit_buttons(
    callback: CallbackQuery,
    reply_markup: InlineKeyboardMarkup | None,
) -> bool:
    """Replace rich-message button blocks, or update a legacy inline keyboard."""
    message = callback.message
    if not message:
        return False
    if getattr(message, "rich_message", None):
        content = rich_content_without_buttons(message)
        if content is None:
            return False
        return await safe_edit(callback, content, reply_markup=reply_markup)
    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
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
