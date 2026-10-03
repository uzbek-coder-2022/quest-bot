"""Best-effort superadmin notifications and shutdown database backups."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.methods import SendDocument
from aiogram.types import FSInputFile

from .backups import database_backup
from .config import Settings
from .database import Database
from .localization import tr

logger = logging.getLogger(__name__)


async def _language(db: Database, user_id: int) -> str:
    try:
        return await db.get_language(user_id)
    except Exception:
        logger.exception("Could not load language for superadmin %s", user_id)
        return "en"


async def _send_message(bot: Bot, db: Database, user_id: int, key: str) -> None:
    language = await _language(db, user_id)
    try:
        await bot.send_message(user_id, tr(language, key))
    except Exception:
        logger.exception("Could not send lifecycle message to superadmin %s", user_id)


async def notify_superadmins_started(bot: Bot, db: Database, settings: Settings) -> None:
    """Send a localized startup notice to every configured superadmin."""
    for user_id in settings.superadmin_ids:
        await _send_message(bot, db, user_id, "service_started")


async def backup_and_notify_superadmins_stopping(
    bot: Bot, db: Database, settings: Settings
) -> None:
    """Notify superadmins, create a database dump, and deliver it before shutdown."""
    for user_id in settings.superadmin_ids:
        await _send_message(bot, db, user_id, "service_stopping")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = ".dump" if settings.database_dsn.startswith(
        ("postgres://", "postgresql://", "postgresql+asyncpg://")
    ) else ".sqlite3"
    filename = f"quest-bot-database-backup-{timestamp}{suffix}"

    try:
        async with database_backup(settings.database_dsn) as backup_path:
            file_id: str | None = None
            for user_id in settings.superadmin_ids:
                language = await _language(db, user_id)
                document = file_id or FSInputFile(backup_path, filename=filename)
                method = SendDocument(
                    chat_id=user_id,
                    document=document,
                    caption=tr(language, "database_backup_caption", timestamp=timestamp),
                )
                try:
                    result = await bot(method, request_timeout=settings.backup_upload_timeout_seconds)
                    if result.document:
                        file_id = result.document.file_id
                except Exception:
                    logger.exception("Could not deliver database backup to superadmin %s", user_id)
                    await _send_message(bot, db, user_id, "database_backup_delivery_failed")
    except Exception:
        logger.exception("Could not create the shutdown database backup")
        for user_id in settings.superadmin_ids:
            await _send_message(bot, db, user_id, "database_backup_failed")
