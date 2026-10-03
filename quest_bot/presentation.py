"""Rich-text presentation builders and Telegram archive-reference helpers."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.formatting import BlockQuote, Bold, Text

from .localization import tr
from .utils import format_datetime

logger = logging.getLogger(__name__)


def quest_preview(quest: dict[str, Any], language: str, participants: int) -> Text:
    """Build safe Telegram entities for a quest preview and join-confirmation screen."""
    status = "paused" if quest.get("paused_at") else str(quest["status"])
    duration_seconds = int(quest.get("duration_seconds") or 0)
    duration = "—" if duration_seconds == 0 else f"{duration_seconds // 60} min"
    chat_title = quest.get("chat_title") or (
        tr(language, "chat_private_only") if not quest.get("chat_id") else str(quest["chat_id"])
    )
    description = str(quest.get("description") or "—")
    return Text(
        Bold(f"🧭 {quest['title']}"),
        "\n\n",
        Bold(f"📝 {tr(language, 'quest_label_description')}"),
        "\n",
        BlockQuote(description),
        "\n\n",
        Bold(f"📍 {tr(language, 'quest_label_status')}"),
        ": ",
        tr(language, f"status_{status}"),
        "\n",
        Bold(f"🌐 {tr(language, 'quest_label_visibility')}"),
        ": ",
        tr(language, f"visibility_{quest['visibility']}"),
        "\n",
        Bold(f"🕒 {tr(language, 'quest_label_start')}"),
        ": ",
        format_datetime(quest.get("start_at"), language),
        "\n",
        Bold(f"🧩 {tr(language, 'quest_label_stages')}"),
        ": ",
        str(int(quest.get("stage_count") or 0)),
        "\n",
        Bold(f"👥 {tr(language, 'quest_label_participants')}"),
        ": ",
        str(participants),
        "\n",
        Bold(f"⚙️ {tr(language, 'quest_label_progression')}"),
        ": ",
        tr(language, f"progression_{quest['progression']}"),
        "\n",
        Bold(f"⏳ {tr(language, 'quest_label_duration')}"),
        ": ",
        duration,
        "\n",
        Bold(f"📣 {tr(language, 'quest_label_chat')}"),
        ": ",
        chat_title,
    )


def support_reply(source: str, message: str, ticket_id: int) -> Text:
    """Build a ticket reply that clearly separates its source and user text."""
    return Text(
        Bold(f"🎫 #{ticket_id} · {source}"),
        "\n",
        BlockQuote(message),
    )


def support_notification(
    ticket_id: int, source: str, sender_name: str, message: str
) -> Text:
    """Format an admin notification with unambiguous ticket and sender context."""
    return Text(
        Bold(f"🎫 #{ticket_id} · {source}"),
        "\n",
        Bold(sender_name),
        "\n",
        BlockQuote(message),
    )


def support_history(messages: list[tuple[str, str]]) -> Text:
    """Render ticket history with labels and safely quoted user-provided text."""
    parts: list[Any] = []
    for index, (sender, message) in enumerate(messages):
        if index:
            parts.append("\n\n")
        parts.extend((Bold(sender), ":\n", BlockQuote(message)))
    return Text(*parts)


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
