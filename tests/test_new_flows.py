from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from quest_bot.handlers.common import _join_from_payload, cancel_command
from quest_bot.handlers.creation import (
    cover_photo_received,
    description_received,
    skip_cover_photo,
)
from quest_bot.handlers.quests import (
    browse_filter_callback,
    cancel_quest_join,
    confirm_quest_join,
    my_quests_callback,
    my_quests_command,
    request_quest_join,
    select_answer_quest,
    view_quest_callback,
)
from quest_bot.handlers.support import (
    admin_ticket_reply,
    create_ticket_message,
    user_ticket_reply,
)
from quest_bot.keyboards import (
    admin_home_keyboard,
    join_confirmation_keyboard,
    manage_quest,
    participating_quests_keyboard,
)
from quest_bot.presentation import quest_preview
from quest_bot.states import CreateQuest
from quest_bot.utils import safe_edit


class FakeBot:
    def __init__(self) -> None:
        self.copies: list[dict] = []
        self.messages: list[tuple[int, dict]] = []

    async def copy_message(self, **kwargs):
        self.copies.append(kwargs)
        return SimpleNamespace(message_id=500 + len(self.copies))

    async def send_message(self, chat_id: int, **kwargs):
        self.messages.append((chat_id, kwargs))
        return SimpleNamespace(message_id=800 + len(self.messages))


class FakeState:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})
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


class JoinPreviewDatabase:
    def __init__(self, quest: dict) -> None:
        self.quest = quest

    async def get_language(self, user_id: int) -> str:
        return "en"

    async def get_quest_by_token(self, quest_id: int, token: str):
        return self.quest if quest_id == self.quest["id"] and token == "private-token" else None

    async def get_quest(self, quest_id: int):
        return self.quest if quest_id == self.quest["id"] else None

    async def is_globally_banned(self, user_id: int):
        return False, None

    async def participant(self, quest_id: int, user_id: int):
        return None

    async def participant_count(self, quest_id: int) -> int:
        return 4


class FeatureFlowTests(unittest.IsolatedAsyncioTestCase):
    def _quest(self, **overrides) -> dict:
        quest = {
            "id": 17,
            "owner_id": 90,
            "title": "Night Quest",
            "description": "A safe <b>literal</b> description",
            "visibility": "private",
            "invite_token": "private-token",
            "status": "scheduled",
            "paused_at": None,
            "progression": "scheduled",
            "start_at": "2026-10-05T12:00:00+00:00",
            "duration_seconds": 3600,
            "chat_id": None,
            "stage_count": 3,
            "cover_chat_id": -100123,
            "cover_message_id": 654,
        }
        quest.update(overrides)
        return quest

    async def test_private_deep_link_shows_full_preview_and_requires_explicit_confirmation(self) -> None:
        quest = self._quest()
        db = JoinPreviewDatabase(quest)
        bot = FakeBot()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
            bot=bot,
        )

        handled = await _join_from_payload(
            message, "q_17_private-token", db, bot
        )

        self.assertTrue(handled)
        self.assertEqual(
            bot.copies,
            [{
                "chat_id": 42,
                "from_chat_id": -100123,
                "message_id": 654,
                "caption": "",
            }],
        )
        answer = message.answer.await_args.kwargs
        self.assertIn("Night Quest", answer["text"])
        self.assertIn("A safe <b>literal</b> description", answer["text"])
        self.assertIn("3", answer["text"])
        self.assertIn("4", answer["text"])
        self.assertIsNone(answer["parse_mode"])
        self.assertTrue(answer["entities"])
        buttons = answer["reply_markup"].inline_keyboard[0]
        self.assertEqual(buttons[0].callback_data, "quest:joinconfirm:17:private-token")
        self.assertEqual(buttons[1].callback_data, "quest:joincancel:17")
        # The start-link handler only renders a preview; the confirmation callback performs the join.
        self.assertFalse(hasattr(db, "join_quest"))

    async def test_my_quest_list_is_private_to_avoid_leaking_private_quest_titles(self) -> None:
        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

        group_message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            chat=SimpleNamespace(type="group"),
            answer=AsyncMock(),
        )
        await my_quests_command(group_message, Db())
        self.assertIn("private chat", group_message.answer.await_args.args[0])

        callback = SimpleNamespace(
            data="quest:mylist:0",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat=SimpleNamespace(type="group")),
            answer=AsyncMock(),
        )
        await my_quests_callback(callback, Db())
        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_public_join_button_opens_details_before_any_join_write(self) -> None:
        quest = self._quest(
            visibility="public", cover_chat_id=None, cover_message_id=None
        )

        class Db(JoinPreviewDatabase):
            async def participant(self, quest_id: int, user_id: int):
                return None

            async def participant_count(self, quest_id: int) -> int:
                return 6

        db = Db(quest)
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private", id=42),
            edit_text=AsyncMock(),
        )
        callback = SimpleNamespace(
            data="quest:join:17",
            from_user=SimpleNamespace(id=42),
            message=message,
            answer=AsyncMock(),
        )

        await request_quest_join(callback, db)

        preview = message.edit_text.await_args.kwargs
        self.assertIn("Night Quest", preview["text"])
        self.assertIn("A safe <b>literal</b> description", preview["text"])
        self.assertIn("6", preview["text"])
        buttons = preview["reply_markup"].inline_keyboard[0]
        self.assertEqual(buttons[0].callback_data, "quest:joinconfirm:17")
        self.assertEqual(buttons[1].callback_data, "quest:joincancel:17")
        self.assertFalse(hasattr(db, "join_quest"))

    async def test_join_cancel_does_not_join_and_returns_public_quest_details(self) -> None:
        quest = self._quest(visibility="public", cover_chat_id=None, cover_message_id=None)

        class Db(JoinPreviewDatabase):
            async def participant(self, quest_id: int, user_id: int):
                return None

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private", id=42),
            edit_text=AsyncMock(),
        )
        callback = SimpleNamespace(
            data="quest:joincancel:17",
            from_user=SimpleNamespace(id=42),
            message=message,
            answer=AsyncMock(),
        )

        await cancel_quest_join(callback, Db(quest))

        self.assertEqual(callback.answer.await_args.args[0], "Joining the quest was cancelled.")
        self.assertIn("Night Quest", message.edit_text.await_args.kwargs["text"])
        self.assertFalse(hasattr(Db, "join_quest"))

    async def test_explicit_confirmation_callback_is_the_join_action(self) -> None:
        quest = self._quest(visibility="public", cover_chat_id=None, cover_message_id=None)

        class Db(JoinPreviewDatabase):
            def __init__(self, value):
                super().__init__(value)
                self.join_args = None

            async def join_quest(self, *args):
                self.join_args = args
                return {"code": "joined", "quest": quest}

            async def log_action(self, *args, **kwargs):
                return None

        db = Db(quest)
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private", id=42),
            answer=AsyncMock(),
            edit_text=AsyncMock(),
        )
        callback = SimpleNamespace(
            data="quest:joinconfirm:17",
            from_user=SimpleNamespace(id=42),
            message=message,
            answer=AsyncMock(),
        )

        await confirm_quest_join(callback, db, FakeBot())

        self.assertEqual(db.join_args[:3], (17, 42, None))
        self.assertIn("You joined the quest", message.answer.await_args.args[0])
        self.assertIsNotNone(message.edit_text.await_args.kwargs["reply_markup"])
        self.assertEqual(callback.answer.await_count, 1)

    async def test_answer_quest_selection_shows_paused_notice_without_entering_answer_state(self) -> None:
        state = FakeState()
        callback = SimpleNamespace(
            data="answer:select:17",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()),
            answer=AsyncMock(),
        )

        class Db:
            async def current_open_stage(self, quest_id: int, user_id: int):
                return {"session_status": "open", "paused_at": "2026-10-03T12:00:00+00:00"}

            async def get_language(self, user_id: int) -> str:
                return "en"

        await select_answer_quest(callback, state, Db())

        self.assertIsNone(state.current_state)
        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])
        self.assertIn("paused", callback.answer.await_args.args[0])

    async def test_creation_description_prompts_for_optional_cover_before_visibility(self) -> None:
        state = FakeState()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=90),
            text="Quest description",
            answer=AsyncMock(),
        )

        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

        await description_received(message, state, Db())

        self.assertEqual(state.current_state, CreateQuest.cover_photo)
        markup = message.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "create:cover:skip")

    async def test_browse_edit_ignores_telegram_message_not_modified_regression(self) -> None:
        message = SimpleNamespace(
            edit_text=AsyncMock(
                side_effect=TelegramBadRequest(
                    method=EditMessageText(chat_id=1, message_id=2, text="same"),
                    message="Bad Request: message is not modified",
                )
            )
        )
        callback = SimpleNamespace(
            data="browse:filter:all:0",
            from_user=SimpleNamespace(id=42),
            message=message,
            answer=AsyncMock(),
        )

        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def settings_get(self, key: str, default: str) -> str:
                return "10"

            async def list_public_quests(self, status, offset, limit):
                return []

        await browse_filter_callback(callback, Db())
        callback.answer.assert_awaited_once()

    async def test_confirmation_keyboard_and_admin_navigation_use_explicit_actions(self) -> None:
        markup = join_confirmation_keyboard("en", 17, "token_value")
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "quest:joinconfirm:17:token_value")
        self.assertEqual(markup.inline_keyboard[0][1].callback_data, "quest:joincancel:17")
        self.assertEqual(admin_home_keyboard("en").inline_keyboard[0][0].callback_data, "admin:home")
        self.assertEqual(
            participating_quests_keyboard("en", [self._quest()]).inline_keyboard[0][0].callback_data,
            "quest:view:my:17",
        )
        manage_markup = manage_quest("en", self._quest(paused_at="2026-10-01T00:00:00+00:00"), "admin")
        callbacks = [button.callback_data for row in manage_markup.inline_keyboard for button in row]
        self.assertIn("manage:resume:17", callbacks)
        self.assertIn("admin:home", callbacks)
        self.assertNotIn("manage:pause:17", callbacks)

    async def test_private_quest_participant_can_open_rich_details(self) -> None:
        quest = self._quest()

        class Db(JoinPreviewDatabase):
            async def get_role(self, user_id: int):
                return None

            async def participant(self, quest_id: int, user_id: int):
                return {"status": "active"}

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private", id=42),
            edit_text=AsyncMock(),
        )
        callback = SimpleNamespace(
            data="quest:view:my:17",
            from_user=SimpleNamespace(id=42),
            message=message,
            answer=AsyncMock(),
        )
        bot = FakeBot()

        await view_quest_callback(callback, Db(quest), bot)

        self.assertEqual(bot.copies[0]["from_chat_id"], -100123)
        edited = message.edit_text.await_args.kwargs
        self.assertIn("Night Quest", edited["text"])
        self.assertIn("4", edited["text"])
        self.assertIsNone(edited["parse_mode"])
        self.assertEqual(
            edited["reply_markup"].inline_keyboard[-1][0].callback_data,
            "quest:mylist:0",
        )

    async def test_cover_photo_is_copied_to_private_archive_and_kept_as_message_reference(self) -> None:
        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def get_role(self, user_id: int) -> str:
                return "admin"

        bot = FakeBot()
        state = FakeState()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=90),
            chat=SimpleNamespace(id=90),
            message_id=1234,
            photo=[object()],
            bot=bot,
            answer=AsyncMock(),
        )
        settings = SimpleNamespace(question_archive_channel_id=-100777)

        await cover_photo_received(message, state, Db(), settings)

        self.assertEqual(bot.copies[0]["chat_id"], -100777)
        self.assertEqual(bot.copies[0]["from_chat_id"], 90)
        self.assertEqual(bot.copies[0]["message_id"], 1234)
        self.assertEqual(state.data["cover_chat_id"], -100777)
        self.assertEqual(state.data["cover_message_id"], 501)
        self.assertEqual(state.current_state, CreateQuest.visibility)

    async def test_cancel_removes_staged_cover_from_private_archive(self) -> None:
        delete_message = AsyncMock()
        state = FakeState({
            "stages": [{"source_chat_id": -100777, "source_message_id": 41}],
            "stage_draft": {"source_chat_id": -100777, "source_message_id": 42},
            "cover_chat_id": -100777,
            "cover_message_id": 43,
        })
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=90),
            bot=SimpleNamespace(delete_message=delete_message),
            answer=AsyncMock(),
        )

        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def get_role(self, user_id: int) -> str:
                return "admin"

        await cancel_command(message, Db(), state)

        self.assertEqual(delete_message.await_count, 3)
        self.assertEqual(
            {call.kwargs["message_id"] for call in delete_message.await_args_list},
            {41, 42, 43},
        )
        markup = message.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "admin:home")

    async def test_optional_cover_can_be_skipped(self) -> None:
        state = FakeState()
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=90),
            message=SimpleNamespace(answer=AsyncMock()),
            answer=AsyncMock(),
        )

        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

        await skip_cover_photo(callback, state, Db())

        self.assertIsNone(state.data["cover_chat_id"])
        self.assertIsNone(state.data["cover_message_id"])
        self.assertEqual(state.current_state, CreateQuest.visibility)

    async def test_support_acknowledgement_has_no_button_but_admin_notice_keeps_reply_action(self) -> None:
        class Db:
            async def create_ticket(self, *args):
                return 31

            async def log_action(self, *args, **kwargs):
                return None

            async def get_ticket(self, ticket_id: int):
                return {
                    "id": 31,
                    "user_id": 20,
                    "target_admin_id": 90,
                    "quest_title": "Silver Quest",
                }

            async def get_user(self, user_id: int):
                return {"full_name": "Player One", "username": "player"}

            async def support_admin_recipients(self, ticket_id: int):
                return [90]

            async def get_language(self, user_id: int):
                return "en"

        message = SimpleNamespace(
            from_user=SimpleNamespace(id=20),
            text="Please help",
            answer=AsyncMock(),
            bot=FakeBot(),
        )
        state = FakeState()

        await create_ticket_message(message, state, Db())

        acknowledgement = message.answer.await_args.kwargs
        self.assertNotIn("reply_markup", acknowledgement)
        self.assertEqual(len(message.bot.messages), 1)
        recipient, notice = message.bot.messages[0]
        self.assertEqual(recipient, 90)
        self.assertIn("Silver Quest", notice["text"])
        self.assertIsNotNone(notice["reply_markup"])
        self.assertEqual(
            notice["reply_markup"].inline_keyboard[0][0].callback_data,
            "support:reply:31",
        )

    async def test_user_followup_ack_has_no_button_and_admin_notice_keeps_ticket_context(self) -> None:
        class Db:
            async def add_ticket_message(self, *args):
                return True

            async def get_ticket(self, ticket_id: int):
                return {
                    "id": 31,
                    "user_id": 20,
                    "target_admin_id": None,
                    "quest_title": None,
                }

            async def get_user(self, user_id: int):
                return {"full_name": "Player One", "username": "player"}

            async def support_admin_recipients(self, ticket_id: int):
                return [1]

            async def get_language(self, user_id: int):
                return "en"

        bot = FakeBot()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=20),
            text="Following up on my question.",
            answer=AsyncMock(),
            bot=bot,
        )
        state = FakeState({"ticket_id": 31})

        await user_ticket_reply(message, state, Db())

        self.assertNotIn("reply_markup", message.answer.await_args.kwargs)
        self.assertEqual(len(bot.messages), 1)
        _, notice = bot.messages[0]
        self.assertIn("#31", notice["text"])
        self.assertIn("Superadmin", notice["text"])
        self.assertEqual(
            notice["reply_markup"].inline_keyboard[0][0].callback_data,
            "support:reply:31",
        )

    async def test_admin_replies_are_attributed_to_quest_admin_or_superadmin(self) -> None:
        class Db:
            async def support_admin_recipients(self, ticket_id: int):
                return [1, 90]

            async def get_ticket(self, ticket_id: int):
                return {
                    "id": 31,
                    "user_id": 20,
                    "target_admin_id": 90,
                    "quest_title": "Silver Quest",
                    "language": "en",
                }

            async def add_ticket_message(self, *args):
                return True

            async def get_language(self, user_id: int):
                return "en"

        for sender_id, expected_source in (
            (1, "Superadmin"),
            (90, "Quest admin for “Silver Quest”"),
        ):
            bot = FakeBot()
            message = SimpleNamespace(
                from_user=SimpleNamespace(id=sender_id),
                text="We are looking into this.",
                answer=AsyncMock(),
                bot=bot,
            )
            state = FakeState({"ticket_id": 31})
            await admin_ticket_reply(message, state, Db())
            self.assertEqual(len(bot.messages), 1)
            target, reply = bot.messages[0]
            self.assertEqual(target, 20)
            self.assertIn(expected_source, reply["text"])
            self.assertIn("#31", reply["text"])
            self.assertIsNotNone(reply["reply_markup"])
            self.assertNotIn("reply_markup", message.answer.await_args.kwargs)

    async def test_safe_rich_text_keeps_user_markup_literal(self) -> None:
        kwargs = quest_preview(self._quest(), "en", 4).as_kwargs()
        self.assertIn("<b>literal</b>", kwargs["text"])
        self.assertEqual(kwargs["parse_mode"], None)
        self.assertTrue(any(entity.type == "blockquote" for entity in kwargs["entities"]))
        self.assertTrue(any(entity.type == "bold" for entity in kwargs["entities"]))

    async def test_safe_edit_reports_successful_message_updates(self) -> None:
        message = SimpleNamespace(edit_text=AsyncMock())
        callback = SimpleNamespace(message=message)

        self.assertTrue(await safe_edit(callback, "updated"))
        message.edit_text.assert_awaited_once_with(text="updated", reply_markup=None)

    async def test_safe_edit_ignores_only_message_not_modified(self) -> None:
        message = SimpleNamespace(
            edit_text=AsyncMock(
                side_effect=TelegramBadRequest(
                    method=EditMessageText(chat_id=1, message_id=2, text="same"),
                    message="Bad Request: message is not modified",
                )
            )
        )
        callback = SimpleNamespace(message=message)
        changed = await safe_edit(callback, "same")
        self.assertFalse(changed)
        message.edit_text.assert_awaited_once_with(text="same", reply_markup=None)


if __name__ == "__main__":
    unittest.main()
