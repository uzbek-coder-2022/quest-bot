from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quest_bot.database import Database, utc_now


class DatabaseFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite3")
        await self.db.initialize()
        await self.db.seed_superadmins([1])
        await self.db.ensure_user(20, "player", "Test Player")
        self.start_at = "2026-10-01T00:00:00+00:00"

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def create_quest(self, answer_mode: str = "auto", attempts: int = 2) -> int:
        quest = {
            "title": "Test quest",
            "description": "Description",
            "visibility": "public",
            "progression": "immediate",
            "start_at": self.start_at,
            "duration_seconds": 0,
            "chat_id": None,
            "invite_token": "test-token",
        }
        stages = [
            {
                "question": "First question",
                "answer_mode": answer_mode,
                "correct_answer": "Exact",
                "max_attempts": attempts,
                "time_limit_seconds": 0,
                "starts_at": self.start_at,
            },
            {
                "question": "Second question",
                "answer_mode": "auto",
                "correct_answer": "Final",
                "max_attempts": 1,
                "time_limit_seconds": 0,
                "starts_at": self.start_at,
            },
        ]
        return await self.db.create_quest(1, quest, stages)

    async def activate_and_join(self, quest_id: int) -> dict:
        await self.db.set_quest_status(quest_id, "active")
        joined = await self.db.join_quest(quest_id, 20, None, utc_now())
        self.assertEqual(joined["code"], "joined")
        quest = await self.db.get_quest(quest_id)
        stage = await self.db.get_stage(quest_id, 1)
        activated = await self.db.activate_stage_for_participant(quest_id, 20, stage, utc_now())
        self.assertTrue(activated)
        return quest

    async def test_exact_answer_then_immediate_progression(self) -> None:
        quest_id = await self.create_quest()
        await self.activate_and_join(quest_id)

        wrong_case = await self.db.submit_answer(quest_id, 20, "exact", utc_now())
        self.assertEqual(wrong_case["code"], "wrong")
        self.assertEqual(wrong_case["remaining"], 1)

        first = await self.db.submit_answer(quest_id, 20, " Exact ", utc_now())
        self.assertEqual(first["code"], "correct")
        self.assertEqual(first["next_stage_order"], 2)

        stage_two = await self.db.get_stage(quest_id, 2)
        self.assertTrue(await self.db.activate_stage_for_participant(quest_id, 20, stage_two, utc_now()))
        final = await self.db.submit_answer(quest_id, 20, "Final", utc_now())
        self.assertEqual(final["code"], "correct")
        self.assertTrue(final["final"])
        participant = await self.db.participant(quest_id, 20)
        self.assertEqual(participant["status"], "completed")

    async def test_attempt_limit_marks_participant_failed(self) -> None:
        quest_id = await self.create_quest(attempts=1)
        await self.activate_and_join(quest_id)
        result = await self.db.submit_answer(quest_id, 20, "wrong", utc_now())
        self.assertEqual(result["code"], "wrong")
        self.assertTrue(result["exhausted"])
        participant = await self.db.participant(quest_id, 20)
        self.assertEqual(participant["status"], "failed")

    async def test_manual_answer_waits_for_admin_review(self) -> None:
        quest_id = await self.create_quest(answer_mode="manual")
        await self.activate_and_join(quest_id)
        result = await self.db.submit_answer(quest_id, 20, "free text", utc_now())
        self.assertEqual(result["code"], "pending")
        self.assertEqual(len(await self.db.pending_answers(quest_id)), 1)
        answer = await self.db.get_pending_answer(result["answer_id"])
        self.assertIsNotNone(answer)

        reviewed = await self.db.review_answer(result["answer_id"], 1, True, utc_now())
        self.assertEqual(reviewed["code"], "reviewed")
        self.assertEqual(reviewed["next_stage_order"], 2)
        self.assertEqual(await self.db.pending_answers(quest_id), [])

    async def test_late_manual_rejection_does_not_fail_scheduled_next_stage(self) -> None:
        quest = {
            "title": "Scheduled manual quest",
            "description": "",
            "visibility": "public",
            "progression": "scheduled",
            "start_at": self.start_at,
            "duration_seconds": 0,
            "chat_id": None,
            "invite_token": "scheduled-token",
        }
        stages = [
            {
                "question": "First",
                "answer_mode": "manual",
                "correct_answer": None,
                "max_attempts": 1,
                "time_limit_seconds": 0,
                "starts_at": self.start_at,
            },
            {
                "question": "Second",
                "answer_mode": "auto",
                "correct_answer": "Second answer",
                "max_attempts": 1,
                "time_limit_seconds": 0,
                "starts_at": "2026-10-02T00:00:00+00:00",
            },
        ]
        quest_id = await self.db.create_quest(1, quest, stages)
        await self.db.set_quest_status(quest_id, "active")
        await self.db.join_quest(quest_id, 20, None, utc_now())
        first_stage = await self.db.get_stage(quest_id, 1)
        await self.db.activate_stage_for_participant(quest_id, 20, first_stage, utc_now())
        pending = await self.db.submit_answer(quest_id, 20, "submitted", utc_now())
        second_stage = await self.db.get_stage(quest_id, 2)
        await self.db.activate_stage_for_participant(quest_id, 20, second_stage, utc_now())

        reviewed = await self.db.review_answer(pending["answer_id"], 1, False, utc_now())
        self.assertTrue(reviewed["obsolete"])
        participant = await self.db.participant(quest_id, 20)
        self.assertEqual(participant["status"], "active")
        self.assertEqual(participant["current_stage"], 2)

    async def test_private_quest_requires_invite_token(self) -> None:
        quest = {
            "title": "Private quest",
            "description": "Only invited users",
            "visibility": "private",
            "progression": "scheduled",
            "start_at": self.start_at,
            "duration_seconds": 0,
            "chat_id": None,
            "invite_token": "private-token",
        }
        stages = [{
            "question": "Question",
            "answer_mode": "auto",
            "correct_answer": "Answer",
            "max_attempts": 1,
            "time_limit_seconds": 0,
            "starts_at": self.start_at,
        }]
        quest_id = await self.db.create_quest(1, quest, stages)
        denied = await self.db.join_quest(quest_id, 20, "wrong-token", utc_now())
        self.assertEqual(denied["code"], "invalid_token")
        allowed = await self.db.join_quest(quest_id, 20, "private-token", utc_now())
        self.assertEqual(allowed["code"], "joined")

    async def test_stage_timeout_is_checked_when_answer_arrives(self) -> None:
        quest = {
            "title": "Timed quest",
            "description": "",
            "visibility": "public",
            "progression": "immediate",
            "start_at": self.start_at,
            "duration_seconds": 0,
            "chat_id": None,
            "invite_token": "timed-token",
        }
        stages = [{
            "question": "Quick question",
            "answer_mode": "auto",
            "correct_answer": "Answer",
            "max_attempts": 1,
            "time_limit_seconds": 1,
            "starts_at": self.start_at,
        }]
        quest_id = await self.db.create_quest(1, quest, stages)
        await self.db.set_quest_status(quest_id, "active")
        await self.db.join_quest(quest_id, 20, None, utc_now())
        stage = await self.db.get_stage(quest_id, 1)
        self.assertTrue(await self.db.activate_stage_for_participant(quest_id, 20, stage, "2010-01-01T00:00:00+00:00"))
        result = await self.db.submit_answer(quest_id, 20, "Answer", utc_now())
        self.assertEqual(result["code"], "timeout")
        self.assertEqual((await self.db.participant(quest_id, 20))["status"], "failed")

    async def test_chat_whitelist_invite_and_support_records(self) -> None:
        quest_id = await self.create_quest()
        chat_id = -10012345
        await self.db.register_chat(chat_id, "Test group", "supergroup", 1)
        await self.db.track_chat_member(chat_id, 20, "Test Player", "player", True)
        await self.db.track_chat_member(chat_id, 21, "Other Player", "other", True)
        await self.db.add_chat_whitelist(chat_id, 20, 1)
        self.assertEqual(await self.db.members_to_remove(chat_id), [21])

        link = await self.db.get_or_create_chat_invite(quest_id, 20, chat_id, "https://t.me/+one-use")
        repeated = await self.db.get_or_create_chat_invite(quest_id, 20, chat_id)
        self.assertEqual(link, repeated)
        await self.db.mark_chat_invite_used(chat_id, link)

        ticket_id = await self.db.create_ticket(20, quest_id, None, "Please help")
        self.assertEqual(await self.db.support_admin_recipients(ticket_id), [1])
        self.assertTrue(await self.db.add_ticket_message(ticket_id, 1, "How can we help?"))
        self.assertEqual(len(await self.db.ticket_messages(ticket_id)), 2)
        self.assertEqual((await self.db.get_ticket(ticket_id))["status"], "open")

        exported = await self.db.export_zip()
        self.assertTrue(exported.startswith(b"PK"))

    async def test_configured_superadmins_are_the_source_of_truth(self) -> None:
        await self.db.seed_superadmins([2])
        self.assertIsNone(await self.db.get_role(1))
        self.assertEqual(await self.db.get_role(2), "superadmin")

    async def test_page_size_settings_and_admin_roles(self) -> None:
        self.assertEqual(await self.db.settings_get("page_size", "10"), "10")
        await self.db.settings_set("page_size", "20")
        self.assertEqual(await self.db.settings_get("page_size", "10"), "20")
        self.assertEqual(await self.db.get_role(1), "superadmin")
        self.assertTrue(await self.db.add_admin(21, 1))
        self.assertEqual(await self.db.get_role(21), "admin")
        self.assertTrue(await self.db.remove_admin(21))
        self.assertEqual(await self.db.get_role(21), None)


if __name__ == "__main__":
    unittest.main()
