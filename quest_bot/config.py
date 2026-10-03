"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    superadmin_ids: tuple[int, ...]
    database_path: str = "data/quest_bot.sqlite3"
    scheduler_interval_seconds: int = 10

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("BOT_TOKEN is required")

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

        database_path = os.getenv("DATABASE_PATH", "data/quest_bot.sqlite3").strip()
        interval = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "10"))
        if interval < 1:
            raise ValueError("SCHEDULER_INTERVAL_SECONDS must be at least 1")
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        return cls(token, ids, database_path, interval)
