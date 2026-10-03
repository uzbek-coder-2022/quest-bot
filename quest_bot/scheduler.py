"""Background scheduler for quest starts, scheduled stages, and timeouts."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .config import Settings
from .database import Database, utc_now
from .localization import tr
from .services import (
    cleanup_known_chat_members,
    release_stage,
    send_chat_invites_to_current_participants,
)

logger = logging.getLogger(__name__)


def _overall_deadline(quest: dict) -> datetime | None:
    duration = int(quest.get("duration_seconds") or 0)
    if duration <= 0:
        return None
    start_at = datetime.fromisoformat(quest["start_at"])
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=timezone.utc)
    return start_at + timedelta(seconds=duration)


async def _notify_quest_end(bot: Bot, db: Database, quest_id: int, user_ids: list[int]) -> None:
    for user_id in user_ids:
        try:
            language = await db.get_language(user_id)
            await bot.send_message(user_id, tr(language, "quest_ended"))
        except TelegramAPIError:
            logger.info("Could not send quest end notice to %s", user_id)


async def scheduler_tick(bot: Bot, db: Database, bot_username: str | None = None) -> None:
    """Run one idempotent pass over due quest events."""
    now = utc_now()
    now_dt = datetime.fromisoformat(now)
    if bot_username is None:
        bot_username = (await bot.get_me()).username or ""

    for due_quest in await db.list_scheduled_quests_due(now):
        quest_id = int(due_quest["id"])
        due_deadline = _overall_deadline(due_quest)
        if due_deadline and now_dt >= due_deadline:
            user_ids = await db.set_quest_status(quest_id, "completed")
            await db.log_action(None, "quest.time_limit.completed", "quest", quest_id)
            await _notify_quest_end(bot, db, quest_id, user_ids)
            continue
        started_now = await db.mark_quest_active(quest_id)
        quest = await db.get_quest(quest_id)
        if not quest or quest["status"] != "active":
            continue
        await cleanup_known_chat_members(bot, db, quest)
        if started_now:
            await send_chat_invites_to_current_participants(bot, db, quest)

    active_quests = await db.list_active_quests()
    expired_quest_ids: set[int] = set()
    for quest in active_quests:
        deadline = _overall_deadline(quest)
        if deadline and now_dt >= deadline:
            user_ids = await db.set_quest_status(int(quest["id"]), "completed")
            expired_quest_ids.add(int(quest["id"]))
            await db.log_action(None, "quest.time_limit.completed", "quest", quest["id"])
            await _notify_quest_end(bot, db, int(quest["id"]), user_ids)

    for session in await db.timed_out_sessions(now):
        quest_id = int(session["quest_id"])
        if quest_id in expired_quest_ids:
            continue
        expired = await db.expire_stage(quest_id, int(session["user_id"]), int(session["stage_id"]), now)
        if expired:
            try:
                await bot.send_message(int(session["user_id"]), tr(session["language"], "stage_timeout"))
            except TelegramAPIError:
                logger.info("Could not send timeout notice to %s", session["user_id"])
            await db.log_action(None, "participant.stage.timeout", "quest_participant", f"{quest_id}:{session['user_id']}")

    for quest in await db.list_active_quests():
        if int(quest["id"]) in expired_quest_ids:
            continue
        if quest["progression"] == "scheduled":
            stage = await db.get_latest_due_stage(int(quest["id"]), now)
            if stage:
                await release_stage(bot, db, quest, stage, bot_username, announce=True)
        else:
            stage = await db.get_stage(int(quest["id"]), 1)
            if stage and stage["starts_at"] <= now:
                await release_stage(bot, db, quest, stage, bot_username, announce=True)
        if await db.maybe_complete_quest(int(quest["id"])):
            user_ids = await db.all_participant_ids(int(quest["id"]), ("joined", "active"))
            await _notify_quest_end(bot, db, int(quest["id"]), user_ids)


async def scheduler_loop(bot: Bot, db: Database, settings: Settings) -> None:
    """Keep the event loop responsive while checking scheduled quest events."""
    logger.info("Quest scheduler started with %s second interval", settings.scheduler_interval_seconds)
    bot_username = (await bot.get_me()).username or ""
    while True:
        try:
            await scheduler_tick(bot, db, bot_username)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Quest scheduler tick failed")
        await asyncio.sleep(settings.scheduler_interval_seconds)
