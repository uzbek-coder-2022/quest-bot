from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quest_bot.database import Database, utc_now
from quest_bot.localization import tr


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

    async def create_quest(
        self, answer_mode: str = "auto", attempts: int = 2, time_limit: int = 0
    ) -> int:
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
                "time_limit_seconds": time_limit,
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

    async def test_pausing_freezes_stage_timer_and_shifts_every_deadline_on_resume(self) -> None:
        quest_id = await self.create_quest(time_limit=1200)
        await self.db.set_quest_status(quest_id, "active")
        await self.db.join_quest(quest_id, 20, None, "2026-10-01T00:00:00+00:00")
        stage = await self.db.get_stage(quest_id, 1)
        self.assertTrue(
            await self.db.activate_stage_for_participant(
                quest_id, 20, stage, "2026-10-01T00:10:00+00:00"
            )
        )

        paused_at = "2026-10-01T00:20:00+00:00"
        self.assertTrue(await self.db.pause_quest(quest_id, 1, paused_at))
        self.assertFalse(await self.db.pause_quest(quest_id, 1, "2026-10-01T00:21:00+00:00"))
        self.assertEqual(await self.db.list_active_quests(), [])
        self.assertEqual(await self.db.timed_out_sessions("2026-10-01T01:00:00+00:00"), [])
        self.assertFalse(
            await self.db.expire_stage(quest_id, 20, int(stage["id"]), "2026-10-01T01:00:00+00:00")
        )
        answer = await self.db.submit_answer(
            quest_id, 20, "Exact", "2026-10-01T01:00:00+00:00"
        )
        self.assertEqual(answer["code"], "paused")
        await self.db.ensure_user(21, "next_player", "Next Player")
        joined = await self.db.join_quest(
            quest_id, 21, None, "2026-10-01T01:00:00+00:00"
        )
        self.assertEqual(joined["code"], "paused")

        resumed_at = "2026-10-01T01:20:00+00:00"
        self.assertEqual(await self.db.resume_quest(quest_id, 1, resumed_at), 3600)
        self.assertIsNone(await self.db.resume_quest(quest_id, 1, resumed_at))
        quest = await self.db.get_quest(quest_id)
        stage = await self.db.get_stage(quest_id, 1)
        self.assertEqual(quest["start_at"], "2026-10-01T01:00:00+00:00")
        self.assertEqual(stage["starts_at"], "2026-10-01T01:00:00+00:00")
        async with self.db._connection() as connection:
            cursor = await connection.execute(
                "SELECT started_at FROM participant_stages WHERE quest_id=? AND user_id=20 AND stage_id=?",
                (quest_id, stage["id"]),
            )
            session = await cursor.fetchone()
        self.assertEqual(session["started_at"], "2026-10-01T01:10:00+00:00")
        self.assertEqual(len(await self.db.list_active_quests()), 1)
        self.assertEqual(await self.db.timed_out_sessions("2026-10-01T01:25:00+00:00"), [])
        self.assertEqual(len(await self.db.timed_out_sessions("2026-10-01T01:31:00+00:00")), 1)

    async def test_pause_shifts_pending_review_timer_before_rejection_reopens_stage(self) -> None:
        quest_id = await self.create_quest(answer_mode="manual", time_limit=1200)
        await self.db.set_quest_status(quest_id, "active")
        await self.db.join_quest(quest_id, 20, None, "2026-10-01T00:00:00+00:00")
        stage = await self.db.get_stage(quest_id, 1)
        self.assertTrue(
            await self.db.activate_stage_for_participant(
                quest_id, 20, stage, "2026-10-01T00:10:00+00:00"
            )
        )
        answer = await self.db.submit_answer(
            quest_id, 20, "needs review", "2026-10-01T00:15:00+00:00"
        )
        self.assertEqual(answer["code"], "pending")
        self.assertTrue(await self.db.pause_quest(quest_id, 1, "2026-10-01T00:20:00+00:00"))
        paused_review = await self.db.review_answer(
            answer["answer_id"], 1, False, "2026-10-01T01:00:00+00:00"
        )
        self.assertEqual(paused_review["code"], "paused")

        self.assertEqual(
            await self.db.resume_quest(quest_id, 1, "2026-10-01T01:20:00+00:00"),
            3600,
        )
        reviewed = await self.db.review_answer(
            answer["answer_id"], 1, False, "2026-10-01T01:21:00+00:00"
        )
        self.assertEqual(reviewed["code"], "reviewed")
        self.assertEqual(reviewed["exhausted"], False)
        self.assertEqual(await self.db.timed_out_sessions("2026-10-01T01:25:00+00:00"), [])
        self.assertEqual(len(await self.db.timed_out_sessions("2026-10-01T01:31:00+00:00")), 1)

    async def test_quest_cover_is_persisted_as_telegram_message_references(self) -> None:
        quest = {
            "title": "Covered quest",
            "description": "An archived cover photo",
            "visibility": "public",
            "progression": "immediate",
            "start_at": self.start_at,
            "duration_seconds": 0,
            "chat_id": None,
            "cover_chat_id": -1001234567890,
            "cover_message_id": 4321,
            "invite_token": "cover-reference-token",
        }
        quest_id = await self.db.create_quest(
            1,
            quest,
            [{
                "question": "Question",
                "answer_mode": "auto",
                "correct_answer": "Answer",
                "max_attempts": 1,
                "time_limit_seconds": 0,
                "starts_at": self.start_at,
            }],
        )
        saved = await self.db.get_quest(quest_id)
        self.assertEqual(saved["cover_chat_id"], -1001234567890)
        self.assertEqual(saved["cover_message_id"], 4321)

    async def test_quest_metadata_updates_are_atomic_audited_and_available_when_completed(self) -> None:
        quest_id = await self.create_quest()
        await self.db.update_quest_metadata(
            quest_id,
            {"cover_chat_id": -1001234567890, "cover_message_id": 321},
            1,
            "2026-10-01T01:00:00+00:00",
        )
        await self.db.set_quest_status(quest_id, "completed")

        previous = await self.db.update_quest_metadata(
            quest_id,
            {
                "title": "Updated title",
                "description": "Updated description",
                "cover_chat_id": -1009876543210,
                "cover_message_id": 654,
            },
            1,
            "2026-10-01T02:00:00+00:00",
        )

        self.assertIsNotNone(previous)
        self.assertEqual(previous["title"], "Test quest")
        self.assertEqual(previous["cover_chat_id"], -1001234567890)
        updated = await self.db.get_quest(quest_id)
        self.assertEqual(updated["title"], "Updated title")
        self.assertEqual(updated["description"], "Updated description")
        self.assertEqual(updated["cover_chat_id"], -1009876543210)
        self.assertEqual(updated["cover_message_id"], 654)
        self.assertEqual(updated["status"], "completed")
        self.assertEqual(updated["updated_at"], "2026-10-01T02:00:00+00:00")
        audit = await self.db.latest_logs(1)
        self.assertEqual(audit[0]["action"], "quest.metadata.updated")
        self.assertEqual(audit[0]["entity_id"], str(quest_id))
        self.assertEqual(audit[0]["actor_id"], 1)

    async def test_quest_metadata_cannot_update_archived_quests_or_partial_cover_refs(self) -> None:
        quest_id = await self.create_quest()
        await self.db.set_quest_status(quest_id, "archived")
        self.assertIsNone(
            await self.db.update_quest_metadata(quest_id, {"title": "Too late"}, 1)
        )
        archived = await self.db.get_quest(quest_id)
        self.assertEqual(archived["title"], "Test quest")

        with self.assertRaises(ValueError):
            await self.db.update_quest_metadata(quest_id, {"cover_message_id": 9}, 1)
        with self.assertRaises(ValueError):
            await self.db.update_quest_metadata(
                quest_id, {"cover_chat_id": None, "cover_message_id": 9}, 1
            )
        with self.assertRaises(ValueError):
            await self.db.update_quest_metadata(quest_id, {"private": True}, 1)
        with self.assertRaises(ValueError):
            await self.db.update_quest_metadata(quest_id, {"title": " "}, 1)
        with self.assertRaises(ValueError):
            await self.db.update_quest_metadata(quest_id, {"description": "x" * 1001}, 1)

    async def test_paused_scheduled_quest_is_not_due_and_start_is_shifted(self) -> None:
        quest = {
            "title": "Paused schedule",
            "description": "",
            "visibility": "public",
            "progression": "scheduled",
            "start_at": "2030-01-01T00:00:00+00:00",
            "duration_seconds": 3600,
            "chat_id": None,
            "invite_token": "pause-scheduled-token",
        }
        stages = [{
            "question": "Question",
            "answer_mode": "auto",
            "correct_answer": "Answer",
            "max_attempts": 1,
            "time_limit_seconds": 0,
            "starts_at": "2030-01-01T00:30:00+00:00",
        }]
        quest_id = await self.db.create_quest(1, quest, stages)
        self.assertTrue(await self.db.pause_quest(quest_id, 1, "2029-12-31T23:00:00+00:00"))
        self.assertEqual(await self.db.list_scheduled_quests_due("2030-01-02T00:00:00+00:00"), [])
        self.assertEqual(
            await self.db.resume_quest(quest_id, 1, "2030-01-01T01:00:00+00:00"),
            7200,
        )
        quest = await self.db.get_quest(quest_id)
        stage = await self.db.get_stage(quest_id, 1)
        self.assertEqual(quest["start_at"], "2030-01-01T02:00:00+00:00")
        self.assertEqual(stage["starts_at"], "2030-01-01T02:30:00+00:00")
        self.assertEqual(await self.db.list_scheduled_quests_due("2030-01-01T01:59:59+00:00"), [])
        self.assertEqual(len(await self.db.list_scheduled_quests_due("2030-01-01T02:00:00+00:00")), 1)

    async def test_joined_quest_list_includes_private_quests_and_counts_participants(self) -> None:
        quest = {
            "title": "Private joined quest",
            "description": "Only participants see this",
            "visibility": "private",
            "progression": "scheduled",
            "start_at": self.start_at,
            "duration_seconds": 0,
            "chat_id": None,
            "invite_token": "private-list-token",
        }
        quest_id = await self.db.create_quest(
            1,
            quest,
            [{
                "question": "Question",
                "answer_mode": "auto",
                "correct_answer": "Answer",
                "max_attempts": 1,
                "time_limit_seconds": 0,
                "starts_at": self.start_at,
            }],
        )
        self.assertEqual(
            (await self.db.join_quest(quest_id, 20, "private-list-token", utc_now()))["code"],
            "joined",
        )
        listed = await self.db.list_user_quests(20)
        self.assertEqual([item["id"] for item in listed], [quest_id])
        self.assertEqual(listed[0]["visibility"], "private")
        self.assertEqual(await self.db.participant_count(quest_id), 1)
        await self.db.set_participant_block(quest_id, 20, "blocked", True)
        self.assertEqual(await self.db.list_user_quests(20), [])
        self.assertEqual(await self.db.participant_count(quest_id), 0)

    async def test_per_quest_leaderboard_returns_grouped_user_details(self) -> None:
        quest_id = await self.create_quest()
        await self.activate_and_join(quest_id)
        result = await self.db.submit_answer(quest_id, 20, "Exact", utc_now())
        self.assertEqual(result["code"], "correct")
        rows = await self.db.leaderboard(quest_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["full_name"], "Test Player")
        self.assertEqual(rows[0]["username"], "player")
        self.assertEqual(rows[0]["solved"], 1)

    async def test_statistics_include_activity_languages_and_completion_metrics(self) -> None:
        await self.db.ensure_user(21, "old_user", "Old User")
        async with self.db._connection() as connection:
            await connection.execute(
                "UPDATE users SET created_at=?,last_seen_at=?,language='ru' WHERE telegram_id=21",
                ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
            await connection.commit()
        quest_id = await self.create_quest()
        await self.db.set_quest_status(quest_id, "active")
        await self.db.join_quest(quest_id, 20, None, utc_now())
        await self.db.set_participant_block(quest_id, 20, None, False)
        async with self.db._connection() as connection:
            await connection.execute(
                "UPDATE quest_participants SET status='completed' WHERE quest_id=? AND user_id=20",
                (quest_id,),
            )
            await connection.commit()

        stats = await self.db.statistics()
        self.assertEqual(stats["users"], 3)
        self.assertEqual(stats["new_users_30d"], 2)
        self.assertEqual(stats["paused"], 0)
        self.assertEqual(stats["active_users_7d"], 2)
        self.assertEqual(stats["active_users_30d"], 2)
        self.assertEqual(stats["language_uz"], 2)
        self.assertEqual(stats["language_ru"], 1)
        self.assertEqual(stats["participants"], 1)
        self.assertEqual(stats["participating_users"], 1)
        self.assertEqual(stats["completed_participations"], 1)
        self.assertEqual(stats["participation_completion_percent"], 100)
        rendered = tr("en", "stats", **stats)
        self.assertNotIn("{paused}", rendered)
        self.assertNotIn("{participation_completion_percent}", rendered)

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
        self.assertEqual((await self.db.list_user_quests(20))[0]["id"], quest_id)

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
