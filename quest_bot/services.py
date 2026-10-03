"""Quest delivery and Telegram chat integration helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .database import Database, utc_now
from .localization import tr

logger = logging.getLogger(__name__)


def answer_deep_link(bot_username: str, quest: dict[str, Any]) -> str:
    if quest["visibility"] == "private":
        payload = f"q_{quest['id']}_{quest['invite_token']}"
    else:
        payload = f"play_{quest['id']}"
    return f"https://t.me/{bot_username}?start={payload}"


def stage_message(language: str, quest: dict[str, Any], stage: dict[str, Any]) -> str:
    time_hint = ""
    if int(stage.get("time_limit_seconds") or 0) > 0:
        time_hint = tr(language, "stage_time_hint", minutes=max(1, int(stage["time_limit_seconds"]) // 60))
    return tr(
        language,
        "question_prefix",
        title=quest["title"],
        number=stage["stage_order"],
        question=stage["question"],
        attempts=stage["max_attempts"],
        time=time_hint,
    )


def answer_button(language: str, link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr(language, "btn_answer_privately"), url=link)
    ]])


async def send_stage_to_user(
    bot: Bot,
    db: Database,
    quest: dict[str, Any],
    stage: dict[str, Any],
    user_id: int,
    bot_username: str,
    already_activated: bool = False,
) -> bool:
    """Activate a participant stage and send its private question."""
    if not already_activated and not await db.activate_stage_for_participant(quest["id"], user_id, stage, utc_now()):
        return False
    language = await db.get_language(user_id)
    message = stage_message(language, quest, stage)
    try:
        await bot.send_message(user_id, message)
        await bot.send_message(user_id, tr(language, "send_answer"))
        return True
    except TelegramForbiddenError:
        logger.info("Unable to DM user %s for quest %s", user_id, quest["id"])
    except TelegramAPIError:
        logger.exception("Failed to deliver stage to user %s for quest %s", user_id, quest["id"])
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
    language = await db.get_language(int(quest["owner_id"]))
    try:
        text = stage_message(language, quest, stage)
        link = answer_deep_link(bot_username, quest)
        await bot.send_message(
            int(chat_id),
            text,
            reply_markup=answer_button(language, link),
            disable_notification=False,
        )
    except TelegramAPIError:
        logger.exception("Failed to announce quest %s stage %s", quest["id"], stage["stage_order"])


async def release_stage(
    bot: Bot,
    db: Database,
    quest: dict[str, Any],
    stage: dict[str, Any],
    bot_username: str,
    announce: bool = True,
) -> None:
    """Announce a synchronized stage and deliver it to currently joined participants."""
    if announce:
        await announce_stage(bot, db, quest, stage, bot_username)
    participants = await db.activate_stage_for_quest(int(quest["id"]), stage, utc_now())
    for user_id in participants:
        await send_stage_to_user(bot, db, quest, stage, user_id, bot_username, already_activated=True)


async def send_current_stage_after_join(
    bot: Bot,
    db: Database,
    quest: dict[str, Any],
    user_id: int,
    bot_username: str,
) -> bool:
    """Deliver the current stage to a participant who joins an already active quest."""
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if quest["progression"] == "scheduled":
        stage = await db.get_latest_due_stage(int(quest["id"]), now)
    else:
        stage = await db.get_stage(int(quest["id"]), 1)
    if not stage:
        return False
    return await send_stage_to_user(bot, db, quest, stage, user_id, bot_username)


async def get_chat_invite_for_participant(
    bot: Bot, db: Database, quest: dict[str, Any], user_id: int
) -> str | None:
    """Create or return the participant's one-use, non-expiring chat invite."""
    chat_id = quest.get("chat_id")
    if not chat_id:
        return None
    existing = await db.get_or_create_chat_invite(int(quest["id"]), user_id, int(chat_id))
    if existing:
        return existing
    try:
        invite = await bot.create_chat_invite_link(chat_id=int(chat_id), member_limit=1)
    except TelegramAPIError:
        logger.exception("Failed to create one-person chat invite for quest %s", quest["id"])
        return None
    link = await db.get_or_create_chat_invite(
        int(quest["id"]), user_id, int(chat_id), invite.invite_link
    )
    await db.log_action(user_id, "chat.invite.created", "quest", quest["id"], {"chat_id": int(chat_id)})
    return link


async def send_chat_invites_to_current_participants(bot: Bot, db: Database, quest: dict[str, Any]) -> None:
    """Deliver one-person group links after start-time cleanup has completed."""
    if not quest.get("chat_id"):
        return
    for user_id in await db.all_participant_ids(int(quest["id"])):
        link = await get_chat_invite_for_participant(bot, db, quest, user_id)
        language = await db.get_language(user_id)
        text = tr(language, "invite_link_ready", link=link) if link else tr(language, "invite_unavailable")
        try:
            await bot.send_message(user_id, text)
        except TelegramAPIError:
            logger.info("Could not send chat invite to user %s", user_id)


async def cleanup_known_chat_members(bot: Bot, db: Database, quest: dict[str, Any]) -> int:
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
            logger.warning("Could not remove known user %s from chat %s: %s", user_id, chat_id, exc)
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
