from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quest_bot.database import Database


class StageEditingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "editing.sqlite3")
        await self.db.initialize()
        await self.db.seed_superadmins([1])
        await self.db.ensure_user(20, "player", "Test Player")

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.temp_dir.cleanup()

    async def _create_quest(self, progression: str, token: str) -> int:
        quest = {
            "title": "Editable quest",
            "description": "",
            "visibility": "public",
            "progression": progression,
            "start_at": "2030-01-02T00:00:00+00:00",
            "duration_seconds": 0,
            "chat_id": None,
            "invite_token": token,
        }
        stages = [
            {
                "question": f"Question {order}",
                "answer_mode": "auto",
                "correct_answer": f"Answer {order}",
                "max_attempts": 2,
                "time_limit_seconds": 0,
                "starts_at": (
                    "2030-01-02T00:00:00+00:00"
                    if order == 1 or progression == "immediate"
                    else "2030-01-04T00:00:00+00:00"
                ),
            }
            for order in (1, 2)
        ]
        return await self.db.create_quest(1, quest, stages)

    async def test_scheduled_quest_and_undelivered_immediate_stage_can_be_edited(self) -> None:
        quest_id = await self._create_quest("immediate", "edit-immediate")
        before_start = "2030-01-01T12:00:00+00:00"
        self.assertTrue(await self.db.stage_is_editable(quest_id, 1, before_start))
        self.assertTrue(await self.db.stage_is_editable(quest_id, 2, before_start))

        previous = await self.db.update_stage_question(
            quest_id,
            1,
            "Revised first question",
            -1001234567890,
            501,
            1,
            before_start,
        )
        self.assertIsNone(previous["source_message_id"])
        stage = await self.db.get_stage(quest_id, 1)
        self.assertEqual(stage["question"], "Revised first question")
        self.assertEqual(stage["source_chat_id"], -1001234567890)
        self.assertEqual(stage["source_message_id"], 501)

        await self.db.set_quest_status(quest_id, "active")
        joined = await self.db.join_quest(quest_id, 20, None, "2030-01-02T00:00:00+00:00")
        self.assertEqual(joined["code"], "joined")
        first_stage = await self.db.get_stage(quest_id, 1)
        self.assertTrue(
            await self.db.activate_stage_for_participant(
                quest_id, 20, first_stage, "2030-01-02T00:00:00+00:00"
            )
        )
        self.assertFalse(
            await self.db.stage_is_editable(quest_id, 1, "2030-01-02T00:00:00+00:00")
        )
        self.assertTrue(
            await self.db.stage_is_editable(quest_id, 2, "2030-01-02T00:00:00+00:00")
        )
        self.assertTrue(
            await self.db.update_stage_answer(
                quest_id, 2, "Revised answer", 1, "2030-01-02T00:00:00+00:00"
            )
        )
        self.assertEqual((await self.db.get_stage(quest_id, 2))["correct_answer"], "Revised answer")

        second_stage = await self.db.get_stage(quest_id, 2)
        self.assertTrue(await self.db.claim_stage_announcement(quest_id, second_stage["id"]))
        self.assertFalse(
            await self.db.stage_is_editable(quest_id, 2, "2030-01-02T00:00:00+00:00")
        )

    async def test_due_scheduled_stage_stays_editable_until_released(self) -> None:
        quest_id = await self._create_quest("scheduled", "edit-scheduled")
        await self.db.set_quest_status(quest_id, "active")
        self.assertTrue(await self.db.stage_is_editable(quest_id, 2, "2030-01-04T00:00:00+00:00"))
        stage = await self.db.get_stage(quest_id, 2)
        self.assertTrue(await self.db.claim_stage_announcement(quest_id, stage["id"]))
        self.assertFalse(await self.db.stage_is_editable(quest_id, 2, "2030-01-04T00:00:00+00:00"))

    async def test_released_stage_update_is_rejected(self) -> None:
        quest_id = await self._create_quest("immediate", "edit-locked")
        await self.db.set_quest_status(quest_id, "active")
        await self.db.join_quest(quest_id, 20, None, "2030-01-02T00:00:00+00:00")
        stage = await self.db.get_stage(quest_id, 1)
        await self.db.activate_stage_for_participant(
            quest_id, 20, stage, "2030-01-02T00:00:00+00:00"
        )

        previous = await self.db.update_stage_question(
            quest_id,
            1,
            "Should not be applied",
            -1001234567890,
            777,
            1,
            "2030-01-02T00:00:00+00:00",
        )
        self.assertIsNone(previous)
        self.assertEqual((await self.db.get_stage(quest_id, 1))["question"], "Question 1")
        self.assertFalse(
            await self.db.update_stage_answer(
                quest_id, 1, "Should not be applied", 1, "2030-01-02T00:00:00+00:00"
            )
        )


if __name__ == "__main__":
    unittest.main()
