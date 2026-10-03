"""Run the Telegram quest bot polling process."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from quest_bot.bot_commands import clear_app_admin_commands, configure_bot_commands
from quest_bot.config import Settings
from quest_bot.database import Database
from quest_bot.handlers import admin, chat, common, creation, quests, support
from quest_bot.lifecycle import (
    backup_and_notify_superadmins_stopping,
    notify_superadmins_started,
)
from quest_bot.scheduler import scheduler_loop

logger = logging.getLogger(__name__)


async def set_commands(bot: Bot, db: Database) -> None:
    admins = await db.list_admins()
    await configure_bot_commands(bot, (int(admin["telegram_id"]) for admin in admins))


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
        previous_superadmin_ids = {
            int(admin["telegram_id"])
            for admin in await db.list_admins()
            if admin["role"] == "superadmin"
        }
        await db.seed_superadmins(settings.superadmin_ids)
        removed_superadmin_ids = previous_superadmin_ids.difference(
            settings.superadmin_ids
        )

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

        for user_id in removed_superadmin_ids:
            await clear_app_admin_commands(bot, user_id)
        await set_commands(bot, db)
        scheduler_task = asyncio.create_task(
            scheduler_loop(bot, db, settings), name="quest-scheduler"
        )
        service_started = True
        await notify_superadmins_started(bot, db, settings)
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
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
