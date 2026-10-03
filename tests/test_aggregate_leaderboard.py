from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quest_bot.database import Database


class AggregateLeaderboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "ratings.sqlite3")
        await self.db.initialize()
        await self.db.seed_superadmins([1])
        for user_id in (20, 21, 22, 23, 24):
            await self.db.ensure_user(user_id, f"player{user_id}", f"Player {user_id}")

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.temp_dir.cleanup()

    async def _complete_quest(
        self,
        owner_id: int,
        user_id: int,
        *,
        title: str,
        visibility: str,
        answers: list[str],
        timestamps: list[str],
        token: str,
    ) -> None:
        start_at = "2026-09-01T00:00:00+00:00"
        quest = {
            "title": title,
            "description": "",
            "visibility": visibility,
            "progression": "immediate",
            "start_at": start_at,
            "duration_seconds": 0,
            "chat_id": None,
            "invite_token": token,
        }
        stages = [
            {
                "question": f"Question {index}",
                "answer_mode": "auto",
                "correct_answer": answer,
                "max_attempts": 1,
                "time_limit_seconds": 0,
                "starts_at": start_at,
            }
            for index, answer in enumerate(answers, start=1)
        ]
        quest_id = await self.db.create_quest(owner_id, quest, stages)
        await self.db.set_quest_status(quest_id, "active")
        private_token = token if visibility == "private" else None
        joined = await self.db.join_quest(quest_id, user_id, private_token, start_at)
        self.assertEqual(joined["code"], "joined")

        for stage_order, (answer, completed_at) in enumerate(zip(answers, timestamps), start=1):
            stage = await self.db.get_stage(quest_id, stage_order)
            self.assertIsNotNone(stage)
            activated = await self.db.activate_stage_for_participant(
                quest_id, user_id, stage, start_at
            )
            self.assertTrue(activated)
            result = await self.db.submit_answer(quest_id, user_id, answer, completed_at)
            self.assertEqual(result["code"], "correct")

    async def test_period_scores_count_public_stages_and_tie_break_by_completed_quests(self) -> None:
        period_start = "2026-10-01T00:00:00+00:00"
        period_end = "2026-10-08T00:00:00+00:00"
        await self._complete_quest(
            1,
            20,
            title="One two-stage quest",
            visibility="public",
            answers=["A", "B"],
            timestamps=[period_start, "2026-10-02T00:00:00+00:00"],
            token="public-two-stage",
        )
        await self._complete_quest(
            1,
            21,
            title="First one-stage quest",
            visibility="public",
            answers=["C"],
            timestamps=["2026-10-03T00:00:00+00:00"],
            token="public-one-a",
        )
        await self._complete_quest(
            1,
            21,
            title="Second one-stage quest",
            visibility="public",
            answers=["D"],
            timestamps=["2026-10-04T00:00:00+00:00"],
            token="public-one-b",
        )
        await self._complete_quest(
            1,
            22,
            title="Private quest must not count",
            visibility="private",
            answers=["E", "F", "G"],
            timestamps=[
                "2026-10-02T00:00:00+00:00",
                "2026-10-03T00:00:00+00:00",
                "2026-10-04T00:00:00+00:00",
            ],
            token="private-many-stages",
        )
        await self._complete_quest(
            1,
            23,
            title="Completion at exclusive end",
            visibility="public",
            answers=["H"],
            timestamps=[period_end],
            token="public-at-end",
        )
        await self._complete_quest(
            1,
            24,
            title="Completion before period",
            visibility="public",
            answers=["I"],
            timestamps=["2026-09-30T23:59:59+00:00"],
            token="public-before-period",
        )

        ratings = await self.db.aggregate_leaderboard(period_start, period_end)
        self.assertEqual([row["user_id"] for row in ratings], [21, 20])
        self.assertEqual(ratings[0]["solved"], 2)
        self.assertEqual(ratings[0]["completed_quests"], 2)
        self.assertEqual(ratings[1]["solved"], 2)
        self.assertEqual(ratings[1]["completed_quests"], 1)


if __name__ == "__main__":
    unittest.main()
