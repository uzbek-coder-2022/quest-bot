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


def _text(content) -> str:
    return str(content.model_dump(mode="json", exclude_none=True))


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, object]] = []
        self.documents: list[tuple[int, object, int | None]] = []
        self.captions: list[str] = []

    async def send_rich_message(self, chat_id: int, rich_message) -> None:
        self.messages.append((chat_id, rich_message))

    async def __call__(self, method, request_timeout: int | None = None):
        self.documents.append((method.chat_id, method.document, request_timeout))
        self.captions.append(method.caption)
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
            self.assertIn("ishga tushdi", _text(bot.messages[0][1]))
            self.assertIn("has started", _text(bot.messages[1][1]))

            await backup_and_notify_superadmins_stopping(bot, db, settings)
            self.assertEqual(len(bot.documents), 2)
            self.assertEqual([chat_id for chat_id, _, _ in bot.documents], [1, 2])
            self.assertEqual(bot.documents[0][2], settings.backup_upload_timeout_seconds)
            self.assertEqual(bot.documents[1][1], "saved-backup-file-id")
            self.assertIn("to‘xtatilmoqda", _text(bot.messages[2][1]))
            self.assertIn("is stopping", _text(bot.messages[3][1]))
            # Each superadmin gets a rich backup notice with a searchable tag and a
            # readable local time before the file itself.
            for index in (4, 5):
                notice = _text(bot.messages[index][1])
                self.assertIn("#backup", notice)
                self.assertIn("Asia/Tashkent, UTC+5", notice)
                self.assertRegex(notice, r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}")
            self.assertEqual(len(bot.captions), 2)
            self.assertTrue(all(caption.endswith("#backup") for caption in bot.captions))
            await db.close()


if __name__ == "__main__":
    unittest.main()
