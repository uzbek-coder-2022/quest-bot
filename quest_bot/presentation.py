"""Rich-text presentation builders and Telegram archive-reference helpers."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InputRichMessage

from .localization import tr
from .rich_text import (
    bold,
    bullet_list,
    divider,
    heading,
    paragraph,
    quote,
    rich_message,
)
from .utils import format_datetime

logger = logging.getLogger(__name__)


def quest_preview(
    quest: dict[str, Any], language: str, participants: int
) -> InputRichMessage:
    """Build a structured, safely serialized rich-message quest preview."""
    status = "paused" if quest.get("paused_at") else str(quest["status"])
    duration_seconds = int(quest.get("duration_seconds") or 0)
    duration = "—" if duration_seconds == 0 else f"{duration_seconds // 60} min"
    chat_title = quest.get("chat_title") or (
        tr(language, "chat_private_only")
        if not quest.get("chat_id")
        else str(quest["chat_id"])
    )
    description = str(quest.get("description") or "—")
    details = [
        [
            bold(f"📍 {tr(language, 'quest_label_status')}"),
            ": ",
            tr(language, f"status_{status}"),
        ],
        [
            bold(f"🌐 {tr(language, 'quest_label_visibility')}"),
            ": ",
            tr(language, f"visibility_{quest['visibility']}"),
        ],
        [
            bold(f"🕒 {tr(language, 'quest_label_start')}"),
            ": ",
            format_datetime(quest.get("start_at"), language),
        ],
        [
            bold(f"🧩 {tr(language, 'quest_label_stages')}"),
            ": ",
            str(int(quest.get("stage_count") or 0)),
        ],
        [
            bold(f"👥 {tr(language, 'quest_label_participants')}"),
            ": ",
            str(participants),
        ],
        [
            bold(f"⚙️ {tr(language, 'quest_label_progression')}"),
            ": ",
            tr(language, f"progression_{quest['progression']}"),
        ],
        [bold(f"⏳ {tr(language, 'quest_label_duration')}"), ": ", duration],
        [bold(f"📣 {tr(language, 'quest_label_chat')}"), ": ", str(chat_title)],
    ]
    return rich_message(
        heading(f"🧭 {quest['title']}", size=1),
        heading(f"📝 {tr(language, 'quest_label_description')}", size=4),
        quote(description),
        divider(),
        bullet_list(details),
    )


def guide_message(language: str) -> InputRichMessage:
    """Render the translated guide as a heading, numbered steps, and a footer."""
    lines = tr(language, "guide").splitlines()
    title = lines[0] if lines else tr(language, "btn_guide")
    steps: list[str] = []
    footer: list[str] = []
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) > 2 and stripped[0].isdigit() and stripped[1:3] == ". ":
            steps.append(stripped[3:])
        else:
            footer.append(stripped)
    blocks = [heading(f"📖 {title.removesuffix(':').strip()}", size=1)]
    if steps:
        blocks.append(bullet_list(steps, ordered=True))
    if footer:
        blocks.extend((divider(), paragraph("\n\n".join(footer))))
    return rich_message(*blocks)


def information_message(title: str, body: str | None = None) -> InputRichMessage:
    """Render a short screen title and optional explanatory paragraph."""
    blocks = [heading(title, size=1)]
    if body:
        blocks.append(paragraph(body))
    return rich_message(*blocks)


def support_reply(source: str, message: str, ticket_id: int) -> InputRichMessage:
    """Build a rich ticket reply with sender context and safely quoted user text."""
    return rich_message(
        heading(f"🎫 #{ticket_id}", size=2),
        paragraph(bold(source)),
        quote(message),
    )


def support_notification(
    ticket_id: int, source: str, sender_name: str, message: str
) -> InputRichMessage:
    """Format an admin ticket notification with a distinct user-message quotation."""
    return rich_message(
        heading(f"🎫 #{ticket_id} · {source}", size=2),
        paragraph(bold(sender_name)),
        quote(message),
    )


def support_history(
    messages: list[tuple[str, str]], title: str, empty_text: str
) -> InputRichMessage:
    """Render ticket history as separate quoted message blocks."""
    blocks = [heading(title, size=1)]
    if not messages:
        blocks.append(paragraph(empty_text))
    else:
        blocks.append(divider())
        blocks.extend(
            quote(bold(sender), "\n", message) for sender, message in messages
        )
    return rich_message(*blocks)


def activity_message(
    title: str, rows: list[str], empty_text: str | None = None
) -> InputRichMessage:
    """Render a compact activity or leaderboard screen as a native rich list."""
    blocks = [heading(title, size=1)]
    if rows:
        blocks.append(bullet_list(rows))
    elif empty_text:
        blocks.append(paragraph(empty_text))
    return rich_message(*blocks)


async def copy_quest_cover(bot: Bot, quest: dict[str, Any], chat_id: int) -> bool:
    """Copy a quest's archived Telegram cover to a chat without downloading media."""
    archive_chat_id = quest.get("cover_chat_id")
    archive_message_id = quest.get("cover_message_id")
    if archive_chat_id is None or archive_message_id is None:
        return False
    try:
        await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=int(archive_chat_id),
            message_id=int(archive_message_id),
            caption="",
        )
    except TelegramAPIError:
        logger.warning(
            "Could not copy archived cover for quest %s to chat %s",
            quest.get("id"),
            chat_id,
            exc_info=True,
        )
        return False
    return True
