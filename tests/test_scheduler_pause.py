from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quest_bot.database import Database
from quest_bot.scheduler import scheduler_tick


class FakeBot:
    def __init__(self) -> None:
        self.actions: list[tuple[str, dict]] = []

    async def send_message(self, chat_id: int, **kwargs) -> None:
        self.actions.append(("send_message", {"chat_id": chat_id, **kwargs}))

    async def copy_message(self, **kwargs) -> None:
        self.actions.append(("copy_message", kwargs))

    async def get_me(self):
        raise AssertionError("A username is provided to scheduler_tick in this test")


class SchedulerPauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_tick_does_not_start_or_notify_a_due_paused_quest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "scheduler.sqlite3")
            await db.initialize()
            await db.seed_superadmins([1])
            await db.ensure_user(20, "player", "Player")
            quest_id = await db.create_quest(
                1,
                {
                    "title": "Paused due quest",
                    "description": "",
                    "visibility": "public",
                    "progression": "scheduled",
                    "start_at": "2026-01-01T00:00:00+00:00",
                    "duration_seconds": 60,
                    "chat_id": None,
                    "invite_token": "scheduler-paused-token",
                },
                [{
                    "question": "Question",
                    "answer_mode": "auto",
                    "correct_answer": "Answer",
                    "max_attempts": 1,
                    "time_limit_seconds": 0,
                    "starts_at": "2026-01-01T00:00:00+00:00",
                }],
            )
            await db.join_quest(quest_id, 20, None, "2026-01-01T00:00:00+00:00")
            self.assertTrue(
                await db.pause_quest(quest_id, 1, "2026-01-01T00:00:00+00:00")
            )
            bot = FakeBot()

            await scheduler_tick(bot, db, "test_bot")

            quest = await db.get_quest(quest_id)
            self.assertEqual(quest["status"], "scheduled")
            self.assertIsNotNone(quest["paused_at"])
            self.assertEqual(bot.actions, [])
            await db.close()


if __name__ == "__main__":
    unittest.main()
