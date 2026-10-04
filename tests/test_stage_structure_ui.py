from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from quest_bot.handlers.quests import (
    added_stage_answer_mode_selected,
    added_stage_attempts_received,
    added_stage_correct_answer_received,
    added_stage_question_received,
    added_stage_start_received,
    added_stage_time_limit_received,
    begin_stage_addition,
    confirm_stage_removal_prompt,
    remove_stage_confirmed,
)
from quest_bot.keyboards import (
    add_stage_answer_mode_keyboard,
    editable_stages_keyboard,
    remove_stage_confirmation_keyboard,
)
from quest_bot.states import AddStage


class FakeState:
    def __init__(self) -> None:
        self.data: dict = {}
        self.current_state = None
        self.cleared = False

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, **values) -> None:
        self.data.update(values)

    async def set_state(self, state) -> None:
        self.current_state = state

    async def clear(self) -> None:
        self.cleared = True
        self.data.clear()


class FakeBot:
    def __init__(self) -> None:
        self.copies: list[dict] = []
        self.deletes: list[dict] = []
        self.sent: list[tuple[int, str]] = []

    async def copy_message(self, **kwargs):
        self.copies.append(kwargs)
        return SimpleNamespace(message_id=900 + len(self.copies))

    async def delete_message(self, **kwargs) -> None:
        self.deletes.append(kwargs)

    async def send_message(
        self, chat_id: int, text: str, reply_markup=None
    ) -> None:
        self.sent.append((chat_id, text))


class StageAdditionDatabase:
    def __init__(self, progression: str = "immediate") -> None:
        self.quest = {
            "id": 17,
            "owner_id": 5,
            "title": "Night Quest",
            "status": "scheduled",
            "progression": progression,
            "start_at": "2030-01-02T00:00:00+00:00",
            "duration_seconds": 0,
        }
        self.stages = [
            {"stage_order": 1, "starts_at": "2030-01-02T00:00:00+00:00"},
            {
                "stage_order": 2,
                "starts_at": (
                    "2030-01-04T00:00:00+00:00"
                    if progression == "scheduled"
                    else "2030-01-02T00:00:00+00:00"
                ),
            },
        ]
        self.added: list[dict] = []
        self.removed = False
        self.completion_result = False
        self.completion_checks = 0

    async def get_language(self, user_id: int) -> str:
        return "en"

    async def get_role(self, user_id: int) -> str:
        return "admin"

    async def get_quest(self, quest_id: int):
        return self.quest if quest_id == self.quest["id"] else None

    async def list_quest_stages(self, quest_id: int):
        return [dict(stage) for stage in self.stages]

    async def can_add_stage(self, quest_id: int, now: str) -> bool:
        return len(self.stages) < 30

    async def stage_structure_options(self, quest_id: int, now: str):
        removable = (
            {int(stage["stage_order"]) for stage in self.stages}
            if len(self.stages) > 1
            else set()
        )
        return [dict(stage) for stage in self.stages], removable, len(self.stages) < 30

    async def add_stage(self, quest_id: int, stage: dict, actor_id: int, now: str):
        stage_order = len(self.stages) + 1
        saved = {**stage, "stage_order": stage_order}
        self.stages.append(saved)
        self.added.append(saved)
        return stage_order

    async def can_remove_stage(self, quest_id: int, stage_order: int, now: str):
        return len(self.stages) > 1

    async def stage_is_editable(self, quest_id: int, stage_order: int, now: str):
        return True

    async def remove_stage(self, quest_id: int, stage_order: int, actor_id: int, now: str):
        self.removed = True
        removed = self.stages.pop(stage_order - 1)
        removed.update(source_chat_id=-1001234567890, source_message_id=501)
        for index, stage in enumerate(self.stages, start=1):
            stage["stage_order"] = index
        return removed

    async def maybe_complete_quest(self, quest_id: int) -> bool:
        self.completion_checks += 1
        return self.completion_result

    async def all_participant_ids(self, quest_id: int, statuses: tuple[str, ...]):
        return [42]


class StageStructureUITests(unittest.IsolatedAsyncioTestCase):
    def _callback(self, data: str, bot: FakeBot, user_id: int = 5):
        return SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(
                chat=SimpleNamespace(type="private"),
                answer=AsyncMock(),
                edit_text=AsyncMock(),
                bot=bot,
            ),
            answer=AsyncMock(),
            bot=bot,
        )

    def _message(
        self, bot: FakeBot, text: str | None, message_id: int = 700
    ):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=5),
            chat=SimpleNamespace(id=5, type="private"),
            message_id=message_id,
            text=text,
            caption=None,
            photo=[],
            video=None,
            bot=bot,
            answer=AsyncMock(),
        )

    async def _run_stage_addition(
        self,
        progression: str,
        answer_mode: str = "manual",
        question_media: str = "text",
    ) -> tuple[FakeBot, StageAdditionDatabase, FakeState]:
        db = StageAdditionDatabase(progression)
        bot = FakeBot()
        state = FakeState()
        settings = SimpleNamespace(question_archive_channel_id=-1001234567890)
        callback = self._callback("manage:addstage:17", bot)

        await begin_stage_addition(callback, state, db)
        question_message = self._message(
            bot,
            "New stage question" if question_media == "text" else None,
            message_id=701,
        )
        if question_media in {"photo", "video"}:
            question_message.caption = "New stage question"
            if question_media == "photo":
                question_message.photo = [SimpleNamespace(file_id="stage-photo-file-id")]
            else:
                question_message.video = SimpleNamespace(file_id="stage-video-file-id")
        await added_stage_question_received(question_message, state, db)
        self.assertEqual(state.current_state, AddStage.answer_mode)
        self.assertEqual(bot.copies, [])

        mode_callback = self._callback(
            f"manage:addstage:answer:{answer_mode}", bot
        )
        await added_stage_answer_mode_selected(mode_callback, state, db)
        if answer_mode == "manual":
            self.assertEqual(state.current_state, AddStage.max_attempts)
        else:
            self.assertEqual(state.current_state, AddStage.correct_answer)
            invalid_answer = self._message(bot, "x" * 301)
            await added_stage_correct_answer_received(invalid_answer, state, db)
            self.assertEqual(state.current_state, AddStage.correct_answer)
            correct_answer = self._message(bot, "New correct answer")
            await added_stage_correct_answer_received(correct_answer, state, db)
            self.assertEqual(state.current_state, AddStage.max_attempts)

        invalid_attempts = self._message(bot, "0")
        await added_stage_attempts_received(invalid_attempts, state, db)
        self.assertEqual(state.current_state, AddStage.max_attempts)
        attempts_message = self._message(bot, "4")
        await added_stage_attempts_received(attempts_message, state, db)
        invalid_time = self._message(bot, "-1")
        await added_stage_time_limit_received(invalid_time, state, db, settings)
        self.assertEqual(state.current_state, AddStage.time_limit)
        time_message = self._message(bot, "15")
        await added_stage_time_limit_received(time_message, state, db, settings)
        if progression == "scheduled":
            self.assertEqual(state.current_state, AddStage.start_at)
            self.assertEqual(bot.copies, [])
            invalid_start = self._message(bot, "2030-01-03 10:00")
            await added_stage_start_received(invalid_start, state, db, settings)
            self.assertEqual(bot.copies, [])
            valid_start = self._message(bot, "2030-01-05 10:00")
            await added_stage_start_received(valid_start, state, db, settings)
        return bot, db, state

    async def test_add_stage_uses_full_manual_setup_and_archives_only_at_final_save(self) -> None:
        bot, db, state = await self._run_stage_addition(
            "immediate", question_media="photo"
        )

        self.assertTrue(state.cleared)
        self.assertEqual(len(bot.copies), 1)
        self.assertEqual(
            bot.copies[0],
            {"chat_id": -1001234567890, "from_chat_id": 5, "message_id": 701},
        )
        self.assertEqual(len(db.added), 1)
        added = db.added[0]
        self.assertEqual(added["stage_order"], 3)
        self.assertEqual(added["question_media_type"], "photo")
        self.assertEqual(added["question_file_id"], "stage-photo-file-id")
        self.assertEqual(added["answer_mode"], "manual")
        self.assertIsNone(added["correct_answer"])
        self.assertEqual(added["max_attempts"], 4)
        self.assertEqual(added["time_limit_seconds"], 900)
        self.assertEqual(added["starts_at"], db.quest["start_at"])

    async def test_automatic_stage_requires_and_saves_a_correct_answer(self) -> None:
        bot, db, state = await self._run_stage_addition("immediate", "auto")

        self.assertTrue(state.cleared)
        self.assertEqual(len(bot.copies), 1)
        self.assertEqual(db.added[0]["answer_mode"], "auto")
        self.assertEqual(db.added[0]["correct_answer"], "New correct answer")

    async def test_scheduled_stage_requires_a_later_delivery_time_before_archiving(self) -> None:
        bot, db, state = await self._run_stage_addition("scheduled")

        self.assertTrue(state.cleared)
        self.assertEqual(len(bot.copies), 1)
        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.added[0]["starts_at"], "2030-01-05T05:00:00+00:00")

    async def test_question_edit_keyboard_shows_only_available_structure_controls(self) -> None:
        stages = [{"stage_order": 1}, {"stage_order": 2}]
        markup = editable_stages_keyboard("en", 17, stages, {2}, allow_add_stage=True)
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]

        self.assertIn("manage:removestage:17:2", callbacks)
        self.assertNotIn("manage:removestage:17:1", callbacks)
        self.assertIn("manage:addstage:17", callbacks)
        self.assertIn(
            "manage:removestageconfirm:17:2",
            [
                button.callback_data
                for row in remove_stage_confirmation_keyboard("en", 17, 2).inline_keyboard
                for button in row
            ],
        )
        answer_mode_callbacks = [
            button.callback_data
            for row in add_stage_answer_mode_keyboard("en").inline_keyboard
            for button in row
        ]
        self.assertIn("manage:addstage:answer:auto", answer_mode_callbacks)
        self.assertIn("manage:addstage:answer:manual", answer_mode_callbacks)

    async def test_add_stage_entry_respects_the_thirty_stage_limit(self) -> None:
        db = StageAdditionDatabase()
        db.stages = [
            {"stage_order": order, "starts_at": "2030-01-02T00:00:00+00:00"}
            for order in range(1, 31)
        ]
        bot = FakeBot()
        state = FakeState()
        callback = self._callback("manage:addstage:17", bot)

        await begin_stage_addition(callback, state, db)

        self.assertIsNone(state.current_state)
        self.assertFalse(state.data)
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_stage_removal_requires_confirmation_then_deletes_archive_copy(self) -> None:
        db = StageAdditionDatabase()
        bot = FakeBot()
        callback = self._callback("manage:removestage:17:2", bot)

        await confirm_stage_removal_prompt(callback, db)

        self.assertFalse(db.removed)
        self.assertIn(
            "manage:removestageconfirm:17:2",
            [
                button.callback_data
                for row in callback.message.edit_text.await_args.kwargs[
                    "reply_markup"
                ].inline_keyboard
                for button in row
            ],
        )
        db.completion_result = True
        confirmed = self._callback("manage:removestageconfirm:17:2", bot)
        await remove_stage_confirmed(confirmed, db)

        self.assertTrue(db.removed)
        self.assertEqual(db.completion_checks, 1)
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.sent[0][0], 42)
        self.assertEqual(
            bot.deletes,
            [{"chat_id": -1001234567890, "message_id": 501}],
        )
        self.assertIn(
            "manage:addstage:17",
            [
                button.callback_data
                for row in confirmed.message.edit_text.await_args.kwargs[
                    "reply_markup"
                ].inline_keyboard
                for button in row
            ],
        )


if __name__ == "__main__":
    unittest.main()
