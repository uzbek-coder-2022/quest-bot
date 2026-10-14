"""Best-effort superadmin notifications and shutdown database backups."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.methods import SendDocument
from aiogram.types import FSInputFile, InputRichMessage

from .backups import database_backup
from .config import Settings
from .database import Database
from .feedback import feedback_message
from .localization import tr
from .presentation import BACKUP_TAG, backup_notice
from .utils import LOCAL_TZ

logger = logging.getLogger(__name__)


async def _language(db: Database, user_id: int) -> str:
    try:
        return await db.get_language(user_id)
    except Exception:
        logger.exception("Could not load language for superadmin %s", user_id)
        return "en"


async def _send_rich(
    bot: Bot,
    db: Database,
    user_id: int,
    build: Callable[[str], InputRichMessage],
) -> None:
    language = await _language(db, user_id)
    try:
        await bot.send_rich_message(user_id, build(language))
    except Exception:
        logger.exception("Could not send lifecycle message to superadmin %s", user_id)


def _local_time_text() -> str:
    """Unambiguous local time for superadmins, e.g. ``14.10.2026 12:38:05 (Asia/Tashkent, UTC+5)``."""
    stamp = datetime.now(LOCAL_TZ)
    offset = stamp.utcoffset()
    hours = int(offset.total_seconds() // 3600) if offset else 0
    return f"{stamp:%d.%m.%Y %H:%M:%S} (Asia/Tashkent, UTC{hours:+d})"


def _backup_failure(language: str, title_key: str, body_key: str) -> InputRichMessage:
    return backup_notice(
        tr(language, title_key),
        tr(language, body_key),
        [(tr(language, "backup_time_label"), _local_time_text())],
    )


async def notify_superadmins_started(bot: Bot, db: Database, settings: Settings) -> None:
    """Send a localized startup notice to every configured superadmin."""
    for user_id in settings.superadmin_ids:
        await _send_rich(
            bot,
            db,
            user_id,
            lambda language: feedback_message(language, "service_started", level="info"),
        )


async def backup_and_notify_superadmins_stopping(
    bot: Bot, db: Database, settings: Settings
) -> None:
    """Notify superadmins, create a database dump, and deliver it before shutdown."""
    for user_id in settings.superadmin_ids:
        await _send_rich(
            bot,
            db,
            user_id,
            lambda language: feedback_message(language, "service_stopping", level="info"),
        )

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
                try:
                    await bot.send_rich_message(
                        user_id,
                        backup_notice(
                            tr(language, "backup_created_title"),
                            tr(language, "backup_created_body"),
                            [
                                (tr(language, "backup_time_label"), _local_time_text()),
                                (tr(language, "backup_file_label"), filename),
                            ],
                        ),
                    )
                except Exception:
                    logger.exception("Could not send backup notice to superadmin %s", user_id)
                document = file_id or FSInputFile(backup_path, filename=filename)
                method = SendDocument(
                    chat_id=user_id,
                    document=document,
                    caption=f"{tr(language, 'database_backup_caption', timestamp=timestamp)}\n{BACKUP_TAG}",
                )
                try:
                    result = await bot(method, request_timeout=settings.backup_upload_timeout_seconds)
                    if result.document:
                        file_id = result.document.file_id
                except Exception:
                    logger.exception("Could not deliver database backup to superadmin %s", user_id)
                    await _send_rich(
                        bot,
                        db,
                        user_id,
                        lambda lang: _backup_failure(
                            lang, "backup_delivery_failed_title", "database_backup_delivery_failed"
                        ),
                    )
    except Exception:
        logger.exception("Could not create the shutdown database backup")
        for user_id in settings.superadmin_ids:
            await _send_rich(
                bot,
                db,
                user_id,
                lambda lang: _backup_failure(
                    lang, "backup_failed_title", "database_backup_failed"
                ),
            )
