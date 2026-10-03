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
from quest_bot.error_reporting import (
    UpdateLoggingMiddleware,
    build_error_report,
    handle_update_error,
    notify_superadmins_of_error,
)
from quest_bot.handlers import admin, chat, common, creation, quests, support
from quest_bot.lifecycle import (
    backup_and_notify_superadmins_stopping,
    notify_superadmins_started,
)
from quest_bot.logging_setup import configure_logging
from quest_bot.scheduler import scheduler_loop
from quest_bot.services import validate_question_archive

logger = logging.getLogger(__name__)


async def set_commands(bot: Bot, db: Database) -> None:
    admins = await db.list_admins()
    await configure_bot_commands(bot, (int(admin["telegram_id"]) for admin in admins))


async def main() -> None:
    configure_logging()
    logger.info("Quest Bot process starting")
    try:
        settings = Settings.from_env()
    except Exception:
        logger.exception("Could not load bot configuration")
        raise
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
        bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=None))
        await validate_question_archive(bot, settings.question_archive_channel_id)
        await db.initialize()
        logger.info("Database initialized successfully")
        previous_superadmin_ids = {
            int(admin["telegram_id"])
            for admin in await db.list_admins()
            if admin["role"] == "superadmin"
        }
        await db.seed_superadmins(settings.superadmin_ids)
        removed_superadmin_ids = previous_superadmin_ids.difference(
            settings.superadmin_ids
        )

        dispatcher = Dispatcher(storage=MemoryStorage())
        dispatcher["db"] = db
        dispatcher["settings"] = settings
        dispatcher.errors.register(handle_update_error)
        dispatcher.update.outer_middleware(UpdateLoggingMiddleware())

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
        logger.info("Quest Bot is ready; starting Telegram polling")
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    except Exception as exc:
        logger.exception("Quest Bot process failed")
        if bot:
            report = build_error_report(exc, settings, source="Bot process")
            await notify_superadmins_of_error(bot, settings, report)
        raise
    finally:
        logger.info("Quest Bot graceful shutdown started")
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
