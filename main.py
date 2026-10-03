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
from quest_bot.scheduler import scheduler_loop


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
    db = Database(settings.database_path)
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
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        await dispatcher.storage.close()


if __name__ == "__main__":
    asyncio.run(main())
