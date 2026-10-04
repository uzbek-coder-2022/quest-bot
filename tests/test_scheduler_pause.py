from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from quest_bot.database import Database
from quest_bot.scheduler import scheduler_tick


class FakeBot:
    def __init__(self) -> None:
        self.actions: list[tuple[str, dict]] = []

    async def send_message(self, chat_id: int, **kwargs) -> None:
        self.actions.append(("send_message", {"chat_id": chat_id, **kwargs}))

    async def send_rich_message(self, chat_id: int, rich_message, **kwargs) -> None:
        self.actions.append(
            (
                "send_rich_message",
                {"chat_id": chat_id, "rich_message": rich_message, **kwargs},
            )
        )

    async def copy_message(self, **kwargs) -> None:
        self.actions.append(("copy_message", kwargs))

    async def get_me(self):
        raise AssertionError("A username is provided to scheduler_tick in this test")

    def texts(self) -> list[str]:
        """Every rich message body, rendered as searchable JSON."""
        return [
            str(payload["rich_message"].model_dump(mode="json"))
            for action, payload in self.actions
            if action == "send_rich_message"
        ]

    def buttons(self) -> list[str]:
        return [
            button.callback_data
            for action, payload in self.actions
            if action == "send_rich_message"
            for row in (payload.get("reply_markup") or SimpleNamespace(inline_keyboard=[])).inline_keyboard
            for button in row
        ]


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


class QuestStartNoticeTests(unittest.IsolatedAsyncioTestCase):
    """The scheduler tells every participant that their quest has started."""

    async def _scheduled_quest(self, db: Database, start_at: str, token: str) -> int:
        return await db.create_quest(
            1,
            {
                "title": "Due quest",
                "description": "",
                "visibility": "public",
                "progression": "scheduled",
                "start_at": start_at,
                "duration_seconds": 0,
                "chat_id": None,
                "invite_token": token,
            },
            [
                {
                    "question": "Question",
                    "answer_mode": "auto",
                    "correct_answer": "Answer",
                    "max_attempts": 1,
                    "time_limit_seconds": 0,
                    "starts_at": start_at,
                }
            ],
        )

    async def test_a_due_quest_notifies_its_participants_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "start.sqlite3")
            await db.initialize()
            await db.seed_superadmins([1])
            await db.ensure_user(20, "player", "Player")
            await db.set_language(20, "en")
            quest_id = await self._scheduled_quest(
                db, "2026-01-01T00:00:00+00:00", "start-notice-token"
            )
            await db.join_quest(quest_id, 20, None, "2026-01-01T00:00:00+00:00")
            bot = FakeBot()

            await scheduler_tick(bot, db, "test_bot")

            self.assertEqual((await db.get_quest(quest_id))["status"], "active")
            started = [
                text
                for text in bot.texts()
                if "has started" in text and "Due quest" in text
            ]
            # Exactly one start notice, addressed to the participant.
            self.assertEqual(len(started), 1)
            self.assertEqual(
                [
                    payload["chat_id"]
                    for action, payload in bot.actions
                    if action == "send_rich_message"
                ][0],
                20,
            )
            # The notice invites the participant to open the question and never
            # contains the question itself.
            self.assertIn(f"quest:continue:{quest_id}", bot.buttons())
            self.assertNotIn("Answer", "".join(bot.texts()))

            # A second pass must not duplicate the notice.
            second = FakeBot()
            await scheduler_tick(second, db, "test_bot")
            self.assertEqual(
                [
                    text
                    for text in second.texts()
                    if "has started" in text and "Due quest" in text
                ],
                [],
            )
            await db.close()

    async def test_a_late_joiner_is_told_that_the_quest_started(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "late.sqlite3")
            await db.initialize()
            await db.seed_superadmins([1])
            await db.ensure_user(20, "player", "Player")
            await db.set_language(20, "en")
            quest_id = await self._scheduled_quest(
                db, "2026-01-01T00:00:00+00:00", "late-join-token"
            )
            await db.mark_quest_active(quest_id)
            bot = FakeBot()

            await db.join_quest(quest_id, 20, None, "2026-01-01T00:10:00+00:00")
            from quest_bot.services import notify_participant_stage_ready

            await notify_participant_stage_ready(
                bot, db, await db.get_quest(quest_id), 20
            )

            self.assertEqual(len(bot.texts()), 1)
            self.assertIn("has started", bot.texts()[0])
            self.assertIn(f"quest:continue:{quest_id}", bot.buttons())
            await db.close()


if __name__ == "__main__":
    unittest.main()
