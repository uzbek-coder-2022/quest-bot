from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from quest_bot.config import Settings
from quest_bot.database import Database
from quest_bot.lifecycle import (
    backup_and_notify_superadmins_stopping,
    notify_superadmins_started,
)


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.documents: list[tuple[int, object, int | None]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))

    async def __call__(self, method, request_timeout: int | None = None):
        self.documents.append((method.chat_id, method.document, request_timeout))
        return SimpleNamespace(document=SimpleNamespace(file_id="saved-backup-file-id"))


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_stop_notices_and_backup_are_sent_to_all_superadmins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "quest-bot.sqlite3"
            db = Database(database_path)
            await db.initialize()
            await db.seed_superadmins([1, 2])
            await db.set_language(2, "en")
            settings = Settings(
                bot_token="123456:abcdefghijklmnopqrstuvwxyzABCDEFG12345",
                superadmin_ids=(1, 2),
                database_dsn=str(database_path),
            )
            bot = FakeBot()

            await notify_superadmins_started(bot, db, settings)
            self.assertEqual([chat_id for chat_id, _ in bot.messages], [1, 2])
            self.assertIn("ishga tushdi", bot.messages[0][1])
            self.assertIn("has started", bot.messages[1][1])

            await backup_and_notify_superadmins_stopping(bot, db, settings)
            self.assertEqual(len(bot.documents), 2)
            self.assertEqual([chat_id for chat_id, _, _ in bot.documents], [1, 2])
            self.assertEqual(bot.documents[0][2], settings.backup_upload_timeout_seconds)
            self.assertEqual(bot.documents[1][1], "saved-backup-file-id")
            self.assertIn("to‘xtatilmoqda", bot.messages[2][1])
            self.assertIn("is stopping", bot.messages[3][1])
            await db.close()


if __name__ == "__main__":
    unittest.main()
