"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    superadmin_ids: tuple[int, ...]
    database_dsn: str = "data/quest_bot.sqlite3"
    database_pool_min_size: int = 1
    database_pool_max_size: int = 10
    scheduler_interval_seconds: int = 10
    backup_upload_timeout_seconds: int = 900
    service_stop_timeout_seconds: int = 1800
    question_archive_channel_id: int = 0

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        token = os.getenv("BOT_TOKEN", "").strip()
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{30,}", token):
            raise ValueError("BOT_TOKEN must be a valid token from BotFather")

        try:
            ids = tuple(
                dict.fromkeys(
                    int(value.strip())
                    for value in os.getenv("SUPERADMIN_IDS", "").split(",")
                    if value.strip()
                )
            )
        except ValueError as exc:
            raise ValueError("SUPERADMIN_IDS must contain comma-separated numeric Telegram IDs") from exc
        if not ids:
            raise ValueError("At least one SUPERADMIN_IDS value is required")

        database_url = os.getenv("DATABASE_URL", "").strip()
        database_dsn = database_url or os.getenv("DATABASE_PATH", "data/quest_bot.sqlite3").strip()
        try:
            pool_min_size = int(os.getenv("DATABASE_POOL_MIN_SIZE", "1"))
            pool_max_size = int(os.getenv("DATABASE_POOL_MAX_SIZE", "10"))
            interval = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "10"))
            backup_timeout = int(os.getenv("BACKUP_UPLOAD_TIMEOUT_SECONDS", "900"))
            stop_timeout = int(os.getenv("SERVICE_STOP_TIMEOUT_SECONDS", "1800"))
        except ValueError as exc:
            raise ValueError("Database, scheduler, and service timeout settings must be integers") from exc
        try:
            archive_channel_id = int(os.getenv("QUESTION_ARCHIVE_CHANNEL_ID", "").strip())
        except ValueError as exc:
            raise ValueError("QUESTION_ARCHIVE_CHANNEL_ID must be a non-zero numeric Telegram chat ID") from exc
        if archive_channel_id == 0:
            raise ValueError("QUESTION_ARCHIVE_CHANNEL_ID must be a non-zero numeric Telegram chat ID")
        if pool_min_size < 1 or pool_max_size < pool_min_size:
            raise ValueError("Database pool sizes must satisfy 1 <= MIN_SIZE <= MAX_SIZE")
        if interval < 1:
            raise ValueError("SCHEDULER_INTERVAL_SECONDS must be at least 1")
        if backup_timeout < 30:
            raise ValueError("BACKUP_UPLOAD_TIMEOUT_SECONDS must be at least 30")
        if stop_timeout < backup_timeout:
            raise ValueError("SERVICE_STOP_TIMEOUT_SECONDS must be >= BACKUP_UPLOAD_TIMEOUT_SECONDS")
        if not database_url:
            Path(database_dsn).parent.mkdir(parents=True, exist_ok=True)
        return cls(
            token,
            ids,
            database_dsn,
            pool_min_size,
            pool_max_size,
            interval,
            backup_timeout,
            stop_timeout,
            archive_channel_id,
        )
