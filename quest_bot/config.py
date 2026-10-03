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
        except ValueError as exc:
            raise ValueError("Database pool sizes and scheduler interval must be integers") from exc
        if pool_min_size < 1 or pool_max_size < pool_min_size:
            raise ValueError("Database pool sizes must satisfy 1 <= MIN_SIZE <= MAX_SIZE")
        if interval < 1:
            raise ValueError("SCHEDULER_INTERVAL_SECONDS must be at least 1")
        if not database_url:
            Path(database_dsn).parent.mkdir(parents=True, exist_ok=True)
        return cls(token, ids, database_dsn, pool_min_size, pool_max_size, interval)
