from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import aiosqlite

from quest_bot.database import SCHEMA, Database


class DatabaseMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_sqlite_schema_gets_archive_cover_and_pause_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite3"
            old_schema = (
                SCHEMA.replace(
                    "    source_chat_id INTEGER,\n    source_message_id INTEGER,\n",
                    "",
                )
                .replace(
                    "    question_media_type TEXT NOT NULL DEFAULT 'legacy' CHECK (question_media_type IN ('legacy','text','photo','video')),\n    question_file_id TEXT,\n",
                    "",
                )
                .replace("    cover_chat_id INTEGER,\n", "")
                .replace("    cover_message_id INTEGER,\n", "")
                .replace("    cover_file_id TEXT,\n", "")
                .replace("    paused_at TEXT,\n", "")
            )
            async with aiosqlite.connect(path) as connection:
                await connection.executescript(old_schema)
                await connection.commit()

            db = Database(path)
            await db.initialize()
            await db.seed_superadmins([1])
            quest_id = await db.create_quest(
                1,
                {
                    "title": "Migrated quest",
                    "description": "",
                    "visibility": "public",
                    "progression": "immediate",
                    "start_at": "2030-01-01T00:00:00+00:00",
                    "duration_seconds": 0,
                    "chat_id": None,
                    "invite_token": "migrated-token",
                },
                [
                    {
                        "question": "Question",
                        "answer_mode": "auto",
                        "correct_answer": "Answer",
                        "max_attempts": 1,
                        "time_limit_seconds": 0,
                        "starts_at": "2030-01-01T00:00:00+00:00",
                        "source_chat_id": -1001234567890,
                        "source_message_id": 42,
                    }
                ],
            )
            stage = await db.get_stage(quest_id, 1)
            self.assertEqual(stage["source_chat_id"], -1001234567890)
            self.assertEqual(stage["source_message_id"], 42)
            self.assertEqual(stage["question_media_type"], "legacy")
            self.assertIsNone(stage["question_file_id"])
            quest = await db.get_quest(quest_id)
            self.assertIsNone(quest["cover_chat_id"])
            self.assertIsNone(quest["cover_file_id"])
            self.assertIsNone(quest["paused_at"])
            await db.close()


if __name__ == "__main__":
    unittest.main()
