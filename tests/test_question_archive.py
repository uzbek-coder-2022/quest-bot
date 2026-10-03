from __future__ import annotations

import unittest
from types import SimpleNamespace

from quest_bot.services import (
    announce_stage,
    archive_question_message,
    send_stage_to_user,
    stage_message,
    validate_question_archive,
)


class FakeBot:
    def __init__(self) -> None:
        self.copies: list[dict] = []
        self.messages: list[tuple[int, object]] = []

    async def copy_message(self, **kwargs):
        self.copies.append(kwargs)
        return SimpleNamespace(message_id=900 + len(self.copies))

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.messages.append((chat_id, text))

    async def send_rich_message(self, chat_id: int, rich_message, **kwargs) -> None:
        self.messages.append((chat_id, rich_message))

    async def delete_message(self, **kwargs) -> None:
        return None

    async def get_chat(self, chat_id: int):
        return SimpleNamespace(type="channel", username=None)

    async def get_me(self):
        return SimpleNamespace(id=99)

    async def get_chat_member(self, chat_id: int, user_id: int):
        return SimpleNamespace(status="administrator", can_post_messages=True)


class FakeDatabase:
    def __init__(self) -> None:
        self.stage: dict | None = None

    async def get_language(self, user_id: int) -> str:
        return "en"

    async def get_stage(self, quest_id: int, stage_order: int) -> dict | None:
        return self.stage

    async def activate_stage_for_participant(self, quest_id: int, user_id: int, stage: dict, now: str) -> bool:
        return True

    async def claim_stage_announcement(self, quest_id: int, stage_id: int) -> bool:
        return True


class QuestionArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_photo_and_video_messages_are_copied_to_archive_by_reference(self) -> None:
        bot = FakeBot()
        message_variants = [
            SimpleNamespace(chat=SimpleNamespace(id=12), message_id=1, text="Question"),
            SimpleNamespace(chat=SimpleNamespace(id=12), message_id=2, photo=[object()], caption="Photo question"),
            SimpleNamespace(chat=SimpleNamespace(id=12), message_id=3, video=object(), caption="Video question"),
        ]

        for original in message_variants:
            archive_chat_id, archive_message_id = await archive_question_message(bot, original, -10077)
            self.assertEqual(archive_chat_id, -10077)
            self.assertEqual(archive_message_id, 900 + len(bot.copies))

        self.assertEqual(
            [(item["chat_id"], item["from_chat_id"], item["message_id"]) for item in bot.copies],
            [(-10077, 12, 1), (-10077, 12, 2), (-10077, 12, 3)],
        )

    async def test_archived_question_is_copied_to_private_user_and_scheduled_chat(self) -> None:
        bot = FakeBot()
        db = FakeDatabase()
        quest = {
            "id": 7,
            "title": "Archive test",
            "owner_id": 1,
            "visibility": "public",
            "invite_token": "token",
            "progression": "scheduled",
            "chat_id": -10088,
        }
        stage = {
            "id": 13,
            "stage_order": 1,
            "question": "Caption stored as metadata",
            "source_chat_id": -10077,
            "source_message_id": 501,
            "answer_mode": "auto",
            "correct_answer": "answer",
            "max_attempts": 2,
            "time_limit_seconds": 0,
        }
        db.stage = stage

        delivered = await send_stage_to_user(bot, db, quest, stage, 20, "quest_test")
        self.assertTrue(delivered)
        self.assertEqual(bot.copies[0]["chat_id"], 20)
        self.assertEqual(bot.copies[0]["from_chat_id"], -10077)
        self.assertEqual(bot.copies[0]["message_id"], 501)
        self.assertEqual([chat_id for chat_id, _ in bot.messages], [20, 20])

        await announce_stage(bot, db, quest, stage, "quest_test")
        self.assertEqual(bot.copies[1]["chat_id"], -10088)
        self.assertEqual(bot.copies[1]["from_chat_id"], -10077)
        self.assertEqual(bot.copies[1]["message_id"], 501)
        self.assertIsNotNone(bot.copies[1]["reply_markup"])
        self.assertEqual(bot.messages[0][1].blocks[0].text, "Archive test")
        self.assertEqual(bot.messages[2][1].blocks[0].text, "Archive test")

    async def test_text_stage_is_structured_and_keeps_question_markup_literal(self) -> None:
        message = stage_message(
            "en",
            {"title": "Literal quest"},
            {
                "stage_order": 2,
                "question": "Question with <b>literal markup</b>",
                "max_attempts": 3,
                "time_limit_seconds": 120,
            },
        )

        payload = message.model_dump(mode="json", exclude_none=True)
        self.assertEqual(payload["blocks"][0]["type"], "heading")
        self.assertEqual(payload["blocks"][2]["type"], "blockquote")
        self.assertEqual(
            payload["blocks"][2]["blocks"][0]["text"],
            "Question with <b>literal markup</b>",
        )
        self.assertIn("Attempts", str(payload))
        self.assertIn("Time limit: 2 minutes", str(payload))

    async def test_immediate_later_stages_are_not_copied_to_the_group(self) -> None:
        bot = FakeBot()
        quest = {
            "id": 7,
            "title": "Immediate quest",
            "owner_id": 1,
            "visibility": "public",
            "invite_token": "token",
            "progression": "immediate",
            "chat_id": -10088,
        }
        stage = {
            "id": 14,
            "stage_order": 2,
            "source_chat_id": -10077,
            "source_message_id": 502,
            "max_attempts": 2,
            "time_limit_seconds": 0,
        }

        await announce_stage(bot, FakeDatabase(), quest, stage, "quest_test")
        self.assertEqual(bot.copies, [])
        self.assertEqual(bot.messages, [])

    async def test_archive_configuration_requires_a_private_channel_and_posting_admin(self) -> None:
        await validate_question_archive(FakeBot(), -10077)

        class NonAdminBot(FakeBot):
            async def get_chat_member(self, chat_id: int, user_id: int):
                return SimpleNamespace(status="member", can_post_messages=False)

        with self.assertRaisesRegex(RuntimeError, "administrator"):
            await validate_question_archive(NonAdminBot(), -10077)

        class PublicChannelBot(FakeBot):
            async def get_chat(self, chat_id: int):
                return SimpleNamespace(type="channel", username="public_archive")

        with self.assertRaisesRegex(RuntimeError, "private Telegram channel"):
            await validate_question_archive(PublicChannelBot(), -10077)


if __name__ == "__main__":
    unittest.main()
