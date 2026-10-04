"""Quest delivery and Telegram chat integration helpers."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputRichMessage

from .database import Database, utc_now
from .keyboards import continue_keyboard
from .localization import tr
from .rich_text import (
    bold,
    bullet_list,
    divider,
    heading,
    paragraph,
    photo_block,
    quote,
    rich_message,
    video_block,
)

logger = logging.getLogger(__name__)


def answer_deep_link(bot_username: str, quest: dict[str, Any]) -> str:
    if quest["visibility"] == "private":
        payload = f"q_{quest['id']}_{quest['invite_token']}"
    else:
        payload = f"play_{quest['id']}"
    return f"https://t.me/{bot_username}?start={payload}"


def _stage_details(language: str, stage: dict[str, Any]) -> list[list[Any]]:
    details: list[list[Any]] = [
        [bold(f"🎯 {tr(language, 'stage_attempts')}"), ": ", str(stage["max_attempts"])]
    ]
    time_limit = int(stage.get("time_limit_seconds") or 0)
    if time_limit > 0:
        minutes = max(1, time_limit // 60)
        details.append([tr(language, "stage_time_hint", minutes=minutes).strip()])
    return details


def stage_message(
    language: str,
    quest: dict[str, Any],
    stage: dict[str, Any],
    *,
    include_answer_instruction: bool = False,
) -> InputRichMessage:
    """Build one safely structured question, stage metadata, and optional answer instruction."""
    blocks = [
        heading(str(quest["title"]), size=1),
        heading(tr(language, "stage_label", number=stage["stage_order"]), size=3),
        bullet_list(_stage_details(language, stage)),
        divider(),
    ]
    media_type = stage.get("question_media_type", "legacy")
    file_id = stage.get("question_file_id")
    if media_type == "photo" and file_id:
        blocks.append(photo_block(str(file_id)))
    elif media_type == "video" and file_id:
        blocks.append(video_block(str(file_id)))
    question = str(stage.get("question") or "").strip()
    if question:
        blocks.append(quote(question))
    if include_answer_instruction:
        blocks.extend((divider(), paragraph(tr(language, "send_answer"))))
    return rich_message(*blocks)


def stage_meta_message(
    language: str, quest: dict[str, Any], stage: dict[str, Any]
) -> InputRichMessage:
    """Build the metadata sent before copying a legacy archived question."""
    return rich_message(
        heading(str(quest["title"]), size=1),
        heading(tr(language, "stage_label", number=stage["stage_order"]), size=3),
        bullet_list(_stage_details(language, stage)),
    )


def question_media_details(message: Any) -> tuple[str, str | None]:
    """Return the supported question media type and reusable Telegram file ID."""
    if getattr(message, "text", None) is not None:
        return "text", None
    photos = getattr(message, "photo", None)
    if photos:
        return "photo", str(photos[-1].file_id)
    video = getattr(message, "video", None)
    if video:
        return "video", str(video.file_id)
    return "legacy", None


async def archive_telegram_message(
    bot: Bot,
    source_chat_id: int,
    source_message_id: int,
    archive_channel_id: int,
) -> tuple[int, int]:
    """Copy a text or media message into the private archive without downloading it."""
    copied = await bot.copy_message(
        chat_id=archive_channel_id,
        from_chat_id=source_chat_id,
        message_id=source_message_id,
    )
    return int(archive_channel_id), int(copied.message_id)


async def archive_question_message(
    bot: Bot, message: Any, archive_channel_id: int
) -> tuple[int, int]:
    """Copy a received question into the private archive without downloading it."""
    return await archive_telegram_message(
        bot,
        int(message.chat.id),
        int(message.message_id),
        archive_channel_id,
    )


async def notify_quest_end(
    bot: Bot, db: Database, quest_id: int, user_ids: list[int]
) -> None:
    """Notify currently active participants that their quest has ended."""
    for user_id in user_ids:
        try:
            language = await db.get_language(user_id)
            await bot.send_message(user_id, tr(language, "quest_ended"))
        except TelegramAPIError:
            logger.info("Could not send quest end notice to %s", user_id)


async def delete_archived_message(
    bot: Bot, chat_id: int | None, message_id: int | None
) -> None:
    """Remove a superseded archive entry when Telegram still permits it."""
    if chat_id is None or message_id is None:
        return
    try:
        await bot.delete_message(chat_id=int(chat_id), message_id=int(message_id))
    except TelegramAPIError:
        logger.warning(
            "Could not delete superseded question archive message %s", message_id
        )


async def validate_question_archive(bot: Bot, archive_channel_id: int) -> None:
    """Ensure the configured archive is a private channel where the bot can post."""
    chat = await bot.get_chat(archive_channel_id)
    if chat.type != "channel" or getattr(chat, "username", None):
        raise RuntimeError(
            "QUESTION_ARCHIVE_CHANNEL_ID must refer to a private Telegram channel"
        )
    bot_user = await bot.get_me()
    member = await bot.get_chat_member(archive_channel_id, bot_user.id)
    status = str(getattr(member.status, "value", member.status))
    if status not in {"administrator", "creator"}:
        raise RuntimeError(
            "The bot must be an administrator in the question archive channel"
        )
    if status == "administrator" and not bool(
        getattr(member, "can_post_messages", False)
    ):
        raise RuntimeError(
            "The bot needs permission to post messages in the question archive channel"
        )


def answer_button(language: str, link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(language, "btn_answer_privately"), url=link, style="primary"
                )
            ]
        ]
    )


async def send_stage_to_user(
    bot: Bot,
    db: Database,
    quest: dict[str, Any],
    stage: dict[str, Any],
    user_id: int,
    bot_username: str,
    already_activated: bool = False,
) -> bool:
    """Deliver a participant's open stage question and start its timer."""
    quest_id = int(quest["id"])
    if not already_activated and not await db.activate_stage_for_participant(
        quest_id, user_id, stage, utc_now()
    ):
        return False
    stage = await db.get_stage(quest_id, int(stage["stage_order"]))
    if not stage:
        return False
    if not await db.mark_stage_delivered(quest_id, user_id, int(stage["id"]), utc_now()):
        return False
    language = await db.get_language(user_id)
    source_chat_id = stage.get("source_chat_id")
    source_message_id = stage.get("source_message_id")
    media_type = stage.get("question_media_type", "legacy")
    file_id = stage.get("question_file_id")
    embeddable = media_type == "text" or (
        media_type in {"photo", "video"}
        and isinstance(file_id, str)
        and bool(file_id.strip())
    )
    has_archive_reference = source_chat_id is not None and source_message_id is not None
    try:
        if embeddable or not has_archive_reference:
            await bot.send_rich_message(
                user_id,
                stage_message(
                    language, quest, stage, include_answer_instruction=True
                ),
            )
        else:
            await bot.send_rich_message(
                user_id, stage_meta_message(language, quest, stage)
            )
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=int(source_chat_id),
                message_id=int(source_message_id),
            )
            await bot.send_message(user_id, tr(language, "send_answer"))
        return True
    except TelegramForbiddenError:
        logger.info("Unable to DM user %s for quest %s", user_id, quest["id"])
    except TelegramAPIError:
        logger.exception(
            "Failed to deliver stage to user %s for quest %s", user_id, quest["id"]
        )
    return False


async def announce_stage(
    bot: Bot,
    db: Database,
    quest: dict[str, Any],
    stage: dict[str, Any],
    bot_username: str,
) -> None:
    """Publish a stage once to its configured group/channel, when applicable."""
    chat_id = quest.get("chat_id")
    if not chat_id:
        return
    if quest["progression"] == "immediate" and int(stage["stage_order"]) > 1:
        return
    if not await db.claim_stage_announcement(int(quest["id"]), int(stage["id"])):
        return
    stage = await db.get_stage(int(quest["id"]), int(stage["stage_order"]))
    if not stage:
        return
    language = await db.get_language(int(quest["owner_id"]))
    try:
        link = answer_deep_link(bot_username, quest)
        reply_markup = answer_button(language, link)
        source_chat_id = stage.get("source_chat_id")
        source_message_id = stage.get("source_message_id")
        media_type = stage.get("question_media_type", "legacy")
        file_id = stage.get("question_file_id")
        embeddable = media_type == "text" or (
            media_type in {"photo", "video"}
            and isinstance(file_id, str)
            and bool(file_id.strip())
        )
        has_archive_reference = source_chat_id is not None and source_message_id is not None
        if embeddable or not has_archive_reference:
            await bot.send_rich_message(
                int(chat_id),
                stage_message(language, quest, stage),
                reply_markup=reply_markup,
                disable_notification=False,
            )
        else:
            await bot.send_rich_message(
                int(chat_id),
                stage_meta_message(language, quest, stage),
                disable_notification=False,
            )
            await bot.copy_message(
                chat_id=int(chat_id),
                from_chat_id=int(source_chat_id),
                message_id=int(source_message_id),
                reply_markup=reply_markup,
                disable_notification=False,
            )
    except TelegramAPIError:
        logger.exception(
            "Failed to announce quest %s stage %s", quest["id"], stage["stage_order"]
        )


async def notify_quest_started(
    bot: Bot, db: Database, quest: dict[str, Any], user_ids: list[int]
) -> None:
    """Tell participants that a quest started without pushing the first question.

    The question itself is sent only after the participant presses the
    Start/Continue button, either in this notice or in the quest card.
    """
    for user_id in user_ids:
        language = await db.get_language(user_id)
        stage = await db.next_deliverable_stage(int(quest["id"]), user_id, utc_now())
        started = bool(stage) and int(stage["stage_order"]) > 1
        button_label = tr(
            language, "btn_continue_quest" if started else "btn_start_quest"
        )
        blocks = [
            heading(
                tr(language, "quest_started_notice", title=quest["title"]), size=2
            ),
            paragraph(
                tr(
                    language,
                    "quest_started_hint" if stage else "quest_started_waiting",
                    button=button_label,
                )
            ),
        ]
        try:
            await bot.send_rich_message(
                user_id,
                rich_message(*blocks),
                reply_markup=(
                    continue_keyboard(language, int(quest["id"]), started)
                    if stage
                    else None
                ),
            )
        except TelegramAPIError:
            logger.info(
                "Could not send the quest start notice to user %s", user_id
            )


async def notify_stage_available(
    bot: Bot,
    db: Database,
    quest: dict[str, Any],
    stage: dict[str, Any],
    user_ids: list[int],
) -> None:
    """Invite participants to open a stage that just became available."""
    for user_id in user_ids:
        language = await db.get_language(user_id)
        blocks = [
            heading(
                tr(
                    language,
                    "stage_available_notice",
                    title=quest["title"],
                    number=stage["stage_order"],
                ),
                size=2,
            ),
            paragraph(
                tr(
                    language,
                    "stage_continue_hint",
                    button=tr(language, "btn_continue_quest"),
                )
            ),
        ]
        try:
            await bot.send_rich_message(
                user_id,
                rich_message(*blocks),
                reply_markup=continue_keyboard(
                    language, int(quest["id"]), started=True
                ),
            )
        except TelegramAPIError:
            logger.info("Could not send the stage notice to user %s", user_id)


async def notify_next_stage_ready(
    bot: Bot, db: Database, quest: dict[str, Any], user_id: int
) -> None:
    """Point a participant at their next question instead of pushing it."""
    language = await db.get_language(user_id)
    try:
        await bot.send_message(
            user_id,
            tr(language, "correct_next", button=tr(language, "btn_continue_quest")),
            reply_markup=continue_keyboard(
                language, int(quest["id"]), started=True
            ),
        )
    except TelegramAPIError:
        logger.info("Could not send the next-stage notice to user %s", user_id)


async def release_stage(
    bot: Bot,
    db: Database,
    quest: dict[str, Any],
    stage: dict[str, Any],
    bot_username: str,
    announce: bool = True,
) -> None:
    """Announce a due stage and invite participants to open it themselves.

    Scheduled quests activate the stage for every current participant so the
    previous open stage closes on time, but the question text is still delivered
    only when a participant presses Start/Continue. Immediate quests activate
    each participant's stage at that moment.
    """
    if announce:
        await announce_stage(bot, db, quest, stage, bot_username)
    quest_id = int(quest["id"])
    now = utc_now()
    if quest["progression"] == "scheduled":
        participants = await db.activate_stage_for_quest(quest_id, stage, now)
    else:
        participants = await db.all_participant_ids(quest_id)
    if not await db.claim_stage_notice(quest_id, int(stage["id"]), now):
        return
    await notify_stage_available(bot, db, quest, stage, participants)


async def notify_participant_stage_ready(
    bot: Bot, db: Database, quest: dict[str, Any], user_id: int
) -> bool:
    """Invite one participant who joined an already active quest to start it."""
    await notify_quest_started(bot, db, quest, [user_id])
    return True


async def get_chat_invite_for_participant(
    bot: Bot, db: Database, quest: dict[str, Any], user_id: int
) -> str | None:
    """Create or return the participant's one-use, non-expiring chat invite."""
    chat_id = quest.get("chat_id")
    if not chat_id:
        return None
    existing = await db.get_or_create_chat_invite(
        int(quest["id"]), user_id, int(chat_id)
    )
    if existing:
        return existing
    try:
        invite = await bot.create_chat_invite_link(chat_id=int(chat_id), member_limit=1)
    except TelegramAPIError:
        logger.exception(
            "Failed to create one-person chat invite for quest %s", quest["id"]
        )
        return None
    link = await db.get_or_create_chat_invite(
        int(quest["id"]), user_id, int(chat_id), invite.invite_link
    )
    await db.log_action(
        user_id, "chat.invite.created", "quest", quest["id"], {"chat_id": int(chat_id)}
    )
    return link


async def send_chat_invites_to_current_participants(
    bot: Bot, db: Database, quest: dict[str, Any]
) -> None:
    """Deliver one-person group links after start-time cleanup has completed."""
    if not quest.get("chat_id"):
        return
    for user_id in await db.all_participant_ids(int(quest["id"])):
        link = await get_chat_invite_for_participant(bot, db, quest, user_id)
        language = await db.get_language(user_id)
        text = (
            tr(language, "invite_link_ready", link=link)
            if link
            else tr(language, "invite_unavailable")
        )
        try:
            await bot.send_message(user_id, text)
        except TelegramAPIError:
            logger.info("Could not send chat invite to user %s", user_id)


async def cleanup_known_chat_members(
    bot: Bot, db: Database, quest: dict[str, Any]
) -> int:
    """Remove known non-whitelisted members from a configured chat at quest start."""
    chat_id = quest.get("chat_id")
    if not chat_id:
        return 0
    chat = await db.get_managed_chat(int(chat_id))
    if not chat or not chat["cleanup_enabled"]:
        return 0
    if not await db.claim_cleanup(int(quest["id"])):
        return 0

    removed = 0
    for user_id in await db.members_to_remove(int(chat_id)):
        try:
            member = await bot.get_chat_member(int(chat_id), user_id)
            if member.status in {"creator", "administrator", "left", "kicked"}:
                continue
            await bot.ban_chat_member(int(chat_id), user_id)
            await bot.unban_chat_member(int(chat_id), user_id, only_if_banned=True)
            await db.track_chat_member(int(chat_id), user_id, "", None, False)
            removed += 1
        except TelegramAPIError as exc:
            logger.warning(
                "Could not remove known user %s from chat %s: %s", user_id, chat_id, exc
            )
    await db.log_action(
        None,
        "chat.cleanup.completed",
        "quest",
        quest["id"],
        {"chat_id": int(chat_id), "removed_count": removed},
    )
    return removed


def is_active_chat_member(status: str, is_member: bool | None = None) -> bool:
    if status in {"member", "administrator", "creator"}:
        return True
    return status == "restricted" and bool(is_member)
