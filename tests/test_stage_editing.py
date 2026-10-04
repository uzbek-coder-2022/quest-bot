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

    async def _create_quest(
        self, progression: str, token: str, duration_seconds: int = 0
    ) -> int:
        quest = {
            "title": "Editable quest",
            "description": "",
            "visibility": "public",
            "progression": progression,
            "start_at": "2030-01-02T00:00:00+00:00",
            "duration_seconds": duration_seconds,
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
            question_media_type="photo",
            question_file_id="replacement-photo-id",
        )
        self.assertIsNone(previous["source_message_id"])
        stage = await self.db.get_stage(quest_id, 1)
        self.assertEqual(stage["question"], "Revised first question")
        self.assertEqual(stage["question_media_type"], "photo")
        self.assertEqual(stage["question_file_id"], "replacement-photo-id")
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
        editable, removable, can_append = await self.db.stage_structure_options(
            quest_id, "2030-01-02T00:00:00+00:00"
        )
        self.assertEqual([stage["stage_order"] for stage in editable], [2])
        self.assertEqual(removable, {2})
        self.assertTrue(can_append)
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
        self.assertFalse(
            await self.db.can_add_stage(quest_id, "2030-01-02T00:00:00+00:00")
        )
        self.assertFalse(
            await self.db.can_remove_stage(quest_id, 1, "2030-01-02T00:00:00+00:00")
        )
        editable, removable, can_append = await self.db.stage_structure_options(
            quest_id, "2030-01-02T00:00:00+00:00"
        )
        self.assertEqual(editable, [])
        self.assertFalse(removable)
        self.assertFalse(can_append)

    async def test_due_scheduled_stage_stays_editable_until_released(self) -> None:
        quest_id = await self._create_quest("scheduled", "edit-scheduled")
        await self.db.set_quest_status(quest_id, "active")
        self.assertTrue(await self.db.stage_is_editable(quest_id, 2, "2030-01-04T00:00:00+00:00"))
        stage = await self.db.get_stage(quest_id, 2)
        self.assertTrue(await self.db.claim_stage_announcement(quest_id, stage["id"]))
        self.assertFalse(await self.db.stage_is_editable(quest_id, 2, "2030-01-04T00:00:00+00:00"))
        self.assertFalse(await self.db.can_add_stage(quest_id, "2030-01-04T00:00:00+00:00"))
        self.assertFalse(await self.db.can_remove_stage(quest_id, 1, "2030-01-04T00:00:00+00:00"))
        self.assertFalse(await self.db.can_remove_stage(quest_id, 2, "2030-01-04T00:00:00+00:00"))
        editable, removable, can_append = await self.db.stage_structure_options(
            quest_id, "2030-01-04T00:00:00+00:00"
        )
        self.assertEqual([stage["stage_order"] for stage in editable], [1])
        self.assertFalse(removable)
        self.assertFalse(can_append)

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

    async def test_scheduled_stage_can_be_added_and_later_stages_are_compacted_on_removal(self) -> None:
        quest_id = await self._create_quest("scheduled", "edit-stage-structure")
        now = "2030-01-01T12:00:00+00:00"
        self.assertTrue(await self.db.can_add_stage(quest_id, now))
        added_order = await self.db.add_stage(
            quest_id,
            {
                "question": "Question 3",
                "answer_mode": "auto",
                "correct_answer": "Answer 3",
                "max_attempts": 3,
                "time_limit_seconds": 60,
                "starts_at": "2030-01-05T00:00:00+00:00",
                "source_chat_id": -1001234567890,
                "source_message_id": 503,
                "question_media_type": "video",
                "question_file_id": "added-stage-video-id",
            },
            1,
            now,
        )
        self.assertEqual(added_order, 3)
        added_stage = await self.db.get_stage(quest_id, added_order)
        self.assertEqual(added_stage["question_media_type"], "video")
        self.assertEqual(added_stage["question_file_id"], "added-stage-video-id")

        previous = await self.db.update_stage_question(
            quest_id, 2, "Question 2 revised", -1001234567890, 502, 1, now
        )
        self.assertIsNotNone(previous)
        self.assertTrue(await self.db.can_remove_stage(quest_id, 2, now))
        deleted = await self.db.remove_stage(quest_id, 2, 1, now)

        self.assertEqual(deleted["source_message_id"], 502)
        remaining = await self.db.list_quest_stages(quest_id)
        self.assertEqual([stage["stage_order"] for stage in remaining], [1, 2])
        self.assertEqual(remaining[1]["question"], "Question 3")
        self.assertEqual((await self.db.get_quest(quest_id))["stage_count"], 2)
        self.assertTrue(await self.db.can_remove_stage(quest_id, 1, now))
        await self.db.remove_stage(quest_id, 1, 1, now)
        final_stage = await self.db.list_quest_stages(quest_id)
        self.assertEqual(len(final_stage), 1)
        self.assertEqual(final_stage[0]["stage_order"], 1)
        self.assertEqual(final_stage[0]["question"], "Question 3")
        self.assertFalse(await self.db.can_remove_stage(quest_id, 1, now))

    async def test_scheduled_addition_obeys_previous_stage_and_overall_deadline(self) -> None:
        quest_id = await self._create_quest(
            "scheduled", "edit-deadline", duration_seconds=3 * 24 * 60 * 60
        )
        now = "2030-01-01T12:00:00+00:00"
        base_stage = {
            "question": "Deadline stage",
            "answer_mode": "manual",
            "correct_answer": None,
            "max_attempts": 1,
            "time_limit_seconds": 0,
            "source_chat_id": -1001234567890,
            "source_message_id": 900,
        }

        self.assertEqual(
            await self.db.add_stage(
                quest_id,
                {**base_stage, "starts_at": "2030-01-05T00:00:00+00:00"},
                1,
                now,
            ),
            3,
        )
        self.assertIsNone(
            await self.db.add_stage(
                quest_id,
                {**base_stage, "starts_at": "2030-01-05T00:00:01+00:00"},
                1,
                now,
            )
        )
        self.assertIsNone(
            await self.db.add_stage(
                quest_id,
                {**base_stage, "starts_at": "2030-01-05T00:00:00+00:00"},
                1,
                now,
            )
        )

    async def test_addition_stops_at_the_existing_thirty_stage_limit(self) -> None:
        quest_id = await self._create_quest("immediate", "edit-stage-limit")
        now = "2030-01-01T12:00:00+00:00"
        for order in range(3, 31):
            added_order = await self.db.add_stage(
                quest_id,
                {
                    "question": f"Question {order}",
                    "answer_mode": "manual",
                    "correct_answer": None,
                    "max_attempts": 1,
                    "time_limit_seconds": 0,
                    "starts_at": "2030-01-02T00:00:00+00:00",
                    "source_chat_id": -1001234567890,
                    "source_message_id": 1000 + order,
                },
                1,
                now,
            )
            self.assertEqual(added_order, order)
        self.assertEqual(len(await self.db.list_quest_stages(quest_id)), 30)
        self.assertFalse(await self.db.can_add_stage(quest_id, now))
        self.assertIsNone(
            await self.db.add_stage(
                quest_id,
                {
                    "question": "One too many",
                    "answer_mode": "manual",
                    "correct_answer": None,
                    "max_attempts": 1,
                    "time_limit_seconds": 0,
                    "starts_at": "2030-01-02T00:00:00+00:00",
                    "source_chat_id": -1001234567890,
                    "source_message_id": 1031,
                },
                1,
                now,
            )
        )

    async def test_paused_quest_addition_keeps_schedule_shift_behavior(self) -> None:
        quest_id = await self._create_quest("scheduled", "edit-paused-add")
        await self.db.set_quest_status(quest_id, "active")
        pause_time = "2030-01-05T00:00:00+00:00"
        await self.db.pause_quest(quest_id, 1, pause_time)
        now = "2030-01-05T01:00:00+00:00"

        self.assertTrue(await self.db.can_add_stage(quest_id, now))
        added_order = await self.db.add_stage(
            quest_id,
            {
                "question": "Question 3",
                "answer_mode": "manual",
                "correct_answer": None,
                "max_attempts": 3,
                "time_limit_seconds": 60,
                "starts_at": "2030-01-04T12:00:00+00:00",
                "source_chat_id": -1001234567890,
                "source_message_id": 503,
            },
            1,
            now,
        )
        self.assertEqual(added_order, 3)

        self.assertEqual(
            await self.db.resume_quest(
                quest_id, 1, "2030-01-06T00:00:00+00:00"
            ),
            86400,
        )
        self.assertEqual(
            (await self.db.get_stage(quest_id, 3))["starts_at"],
            "2030-01-05T12:00:00+00:00",
        )

    async def test_active_structure_changes_are_limited_to_unreleased_suffix(self) -> None:
        quest_id = await self._create_quest("immediate", "edit-active-structure")
        now = "2030-01-02T00:00:00+00:00"
        await self.db.set_quest_status(quest_id, "active")
        await self.db.join_quest(quest_id, 20, None, now)
        first_stage = await self.db.get_stage(quest_id, 1)
        await self.db.activate_stage_for_participant(quest_id, 20, first_stage, now)

        self.assertTrue(await self.db.can_add_stage(quest_id, now))
        self.assertFalse(await self.db.can_remove_stage(quest_id, 1, now))
        self.assertTrue(await self.db.can_remove_stage(quest_id, 2, now))
        added = await self.db.add_stage(
            quest_id,
            {
                "question": "Question 3",
                "answer_mode": "auto",
                "correct_answer": "Answer 3",
                "max_attempts": 2,
                "time_limit_seconds": 0,
                "starts_at": "2030-01-02T00:00:00+00:00",
                "source_chat_id": -1001234567890,
                "source_message_id": 503,
            },
            1,
            now,
        )
        self.assertEqual(added, 3)
        removed = await self.db.remove_stage(quest_id, 2, 1, now)
        self.assertIsNotNone(removed)
        self.assertEqual(
            [(stage["stage_order"], stage["question"]) for stage in await self.db.list_quest_stages(quest_id)],
            [(1, "Question 1"), (2, "Question 3")],
        )
        participant = await self.db.participant(quest_id, 20)
        self.assertEqual(participant["current_stage"], 1)
        self.assertEqual(participant["status"], "active")

    async def test_removing_final_unreleased_stage_completes_participant_who_finished_previous_one(self) -> None:
        quest_id = await self._create_quest("immediate", "edit-remove-final")
        now = "2030-01-02T00:00:00+00:00"
        await self.db.set_quest_status(quest_id, "active")
        await self.db.join_quest(quest_id, 20, None, now)
        first_stage = await self.db.get_stage(quest_id, 1)
        await self.db.activate_stage_for_participant(quest_id, 20, first_stage, now)
        result = await self.db.submit_answer(
            quest_id, 20, "Answer 1", "2030-01-02T00:01:00+00:00"
        )
        self.assertEqual(result["code"], "correct")
        self.assertTrue(await self.db.can_remove_stage(quest_id, 2, now))

        removed = await self.db.remove_stage(quest_id, 2, 1, now)

        self.assertIsNotNone(removed)
        participant = await self.db.participant(quest_id, 20)
        self.assertEqual(participant["status"], "completed")
        # The quest itself still has no time limit, so it stays open and more
        # players may join even though the first one already finished.
        self.assertFalse(await self.db.maybe_complete_quest(quest_id))
        self.assertEqual((await self.db.get_quest(quest_id))["status"], "active")


class QuestAutoCompletionTests(unittest.IsolatedAsyncioTestCase):
    """A quest closes on schedule, never because the last player finished."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "auto-complete.sqlite3")
        await self.db.initialize()
        await self.db.seed_superadmins([1])

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.temp_dir.cleanup()

    async def _create_quest(self, token: str, duration_seconds: int) -> int:
        quest = {
            "title": "Timed quest",
            "description": "",
            "visibility": "public",
            "progression": "immediate",
            "start_at": "2030-01-02T00:00:00+00:00",
            "duration_seconds": duration_seconds,
            "chat_id": None,
            "invite_token": token,
        }
        stages = [
            {
                "question": "Question 1",
                "answer_mode": "auto",
                "correct_answer": "Answer 1",
                "max_attempts": 2,
                "time_limit_seconds": 0,
                "starts_at": "2030-01-02T00:00:00+00:00",
            }
        ]
        return await self.db.create_quest(1, quest, stages)

    async def _start_in_the_past(self, quest_id: int, start_at: str) -> None:
        await self.db.update_quest_start_at(quest_id, start_at, 1)
        await self.db.set_quest_status(quest_id, "active")

    async def test_a_quest_without_a_time_limit_never_auto_completes(self) -> None:
        quest_id = await self._create_quest("auto-open", 0)
        await self._start_in_the_past(quest_id, "2030-01-02T00:00:00+00:00")

        self.assertFalse(await self.db.maybe_complete_quest(quest_id))
        self.assertEqual((await self.db.get_quest(quest_id))["status"], "active")

    async def test_a_quest_still_accepts_players_inside_its_window(self) -> None:
        quest_id = await self._create_quest("auto-running", 3600)
        await self._start_in_the_past(quest_id, "2030-01-02T00:00:00+00:00")

        self.assertFalse(await self.db.maybe_complete_quest(quest_id))
        self.assertEqual((await self.db.get_quest(quest_id))["status"], "active")
        await self.db.ensure_user(42, "latecomer", "Late Comer")
        await self.db.join_quest(quest_id, 42, None, "2030-01-02T00:10:00+00:00")
        self.assertIsNotNone(await self.db.participant(quest_id, 42))

    async def test_an_admin_can_re_time_a_running_quest(self) -> None:
        quest_id = await self._create_quest("auto-retime", 0)
        await self._start_in_the_past(quest_id, "2030-01-02T00:00:00+00:00")

        previous = await self.db.update_quest_duration(
            quest_id, 7200, 1, "2030-01-02T01:00:00+00:00"
        )

        self.assertIsNotNone(previous)
        self.assertEqual(previous["duration_seconds"], 0)
        self.assertEqual(
            (await self.db.get_quest(quest_id))["duration_seconds"], 7200
        )
        # A finished quest can no longer be re-timed.
        await self.db.set_quest_status(quest_id, "completed")
        self.assertIsNone(
            await self.db.update_quest_duration(
                quest_id, 60, 1, "2030-01-02T02:00:00+00:00"
            )
        )

    async def test_a_quest_is_completed_after_its_end_time(self) -> None:
        quest_id = await self._create_quest("auto-closed", 3600)
        await self._start_in_the_past(quest_id, "2020-01-02T00:00:00+00:00")

        self.assertTrue(await self.db.maybe_complete_quest(quest_id))
        self.assertEqual((await self.db.get_quest(quest_id))["status"], "completed")
        # Completing twice is a no-op: the quest is no longer active.
        self.assertFalse(await self.db.maybe_complete_quest(quest_id))


if __name__ == "__main__":
    unittest.main()
