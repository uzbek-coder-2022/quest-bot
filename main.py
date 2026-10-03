"""Run the Telegram quest bot polling process."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault

from quest_bot.config import Settings
from quest_bot.database import Database
from quest_bot.handlers import admin, chat, common, creation, quests, support
from quest_bot.lifecycle import (
    backup_and_notify_superadmins_stopping,
    notify_superadmins_started,
)
from quest_bot.scheduler import scheduler_loop

logger = logging.getLogger(__name__)


async def set_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Open the bot / Bosh menyu"),
        BotCommand(command="menu", description="Main menu"),
        BotCommand(command="quests", description="Browse public quests"),
        BotCommand(command="admin", description="Admin panel"),
        BotCommand(command="support", description="Contact an administrator"),
        BotCommand(command="language", description="Change interface language"),
        BotCommand(command="help", description="Usage guide"),
        BotCommand(command="cancel", description="Cancel current action"),
        BotCommand(command="chatid", description="Show this group or channel ID"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    db = Database(
        settings.database_dsn,
        pool_min_size=settings.database_pool_min_size,
        pool_max_size=settings.database_pool_max_size,
    )
    bot: Bot | None = None
    dispatcher: Dispatcher | None = None
    scheduler_task: asyncio.Task[None] | None = None
    service_started = False

    try:
        await db.initialize()
        await db.seed_superadmins(settings.superadmin_ids)

        bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=None))
        dispatcher = Dispatcher(storage=MemoryStorage())
        dispatcher["db"] = db
        dispatcher["settings"] = settings

        dispatcher.include_router(common.router)
        dispatcher.include_router(creation.router)
        dispatcher.include_router(support.router)
        dispatcher.include_router(admin.router)
        dispatcher.include_router(quests.router)
        dispatcher.include_router(chat.router)

        await set_commands(bot)
        scheduler_task = asyncio.create_task(scheduler_loop(bot, db, settings), name="quest-scheduler")
        service_started = True
        await notify_superadmins_started(bot, db, settings)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        if scheduler_task:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Quest scheduler stopped with an error")
        if service_started and bot:
            await backup_and_notify_superadmins_stopping(bot, db, settings)
        if bot:
            await bot.session.close()
        if dispatcher:
            await dispatcher.storage.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
