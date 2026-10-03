from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from quest_bot.handlers.quests import (
    begin_quest_cover_photo_edit,
    begin_quest_title_edit,
    cancel_stage_edit,
    confirm_quest_cover_removal_prompt,
    edit_quest_details_menu,
    quest_cover_editor,
    quest_cover_photo_received,
    quest_description_received,
    quest_title_received,
    remove_quest_cover,
)
from quest_bot.keyboards import (
    button,
    confirm_cover_removal_keyboard,
    edit_quest_cover_keyboard,
    edit_quest_details_keyboard,
    join_confirmation_keyboard,
    manage_quest,
)
from quest_bot.localization import LANGUAGES, TEXTS
from quest_bot.states import EditQuestDetails


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
        self.data.clear()
        self.current_state = None
        self.cleared = True


class FakeBot:
    def __init__(self) -> None:
        self.copies: list[dict] = []
        self.deletions: list[dict] = []

    async def copy_message(self, **kwargs):
        self.copies.append(kwargs)
        return SimpleNamespace(message_id=900 + len(self.copies))

    async def delete_message(self, **kwargs):
        self.deletions.append(kwargs)
        return True


class FakeMetadataDatabase:
    def __init__(self, quest: dict | None = None, roles: dict[int, str] | None = None) -> None:
        self.quest = quest or {
            "id": 42,
            "owner_id": 7,
            "status": "active",
            "title": "Original quest",
            "description": "Original description",
            "cover_chat_id": None,
            "cover_message_id": None,
            "cover_file_id": None,
        }
        self.roles = roles or {7: "admin"}
        self.updates: list[dict] = []

    async def get_language(self, user_id: int) -> str:
        return "en"

    async def get_role(self, user_id: int) -> str | None:
        return self.roles.get(user_id)

    async def get_quest(self, quest_id: int) -> dict | None:
        return copy.deepcopy(self.quest) if quest_id == self.quest["id"] else None

    async def update_quest_metadata(
        self, quest_id: int, changes: dict, actor_id: int, now: str | None = None
    ) -> dict | None:
        self.updates.append(dict(changes))
        if quest_id != self.quest["id"] or self.quest["status"] == "archived":
            return None
        previous = copy.deepcopy(self.quest)
        self.quest.update(changes)
        return previous


def make_callback(data: str, bot: FakeBot | None = None, user_id: int = 7):
    bot = bot or FakeBot()
    message = SimpleNamespace(
        chat=SimpleNamespace(id=user_id, type="private"),
        bot=bot,
        edit_text=AsyncMock(),
        answer=AsyncMock(),
        answer_rich=AsyncMock(),
    )
    callback = SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=message,
        answer=AsyncMock(),
    )
    return callback, message, bot


def make_photo_message(state_data: dict, bot: FakeBot):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=7),
        message_id=105,
        photo=[SimpleNamespace(file_id="telegram-file-id")],
        text=None,
        bot=bot,
        answer=AsyncMock(),
        answer_rich=AsyncMock(),
    )


class QuestMetadataEditingTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_entry_is_available_for_completed_but_not_archived_quests(self) -> None:
        db = FakeMetadataDatabase({
            "id": 42,
            "owner_id": 7,
            "status": "completed",
            "title": "Finished quest",
            "description": "Description",
            "cover_chat_id": None,
            "cover_message_id": None,
        })
        callback, message, _ = make_callback("manage:editdetails:42")

        await edit_quest_details_menu(callback, db)

        edited = message.edit_text.await_args.kwargs
        self.assertIsNotNone(edited["reply_markup"])
        self.assertIn(
            "Finished quest",
            str(edited["rich_message"].model_dump(mode="json", exclude_none=True)),
        )
        self.assertEqual(callback.answer.await_count, 1)

        db.quest["status"] = "archived"
        callback, message, _ = make_callback("manage:editdetails:42")
        await edit_quest_details_menu(callback, db)
        self.assertFalse(message.edit_text.await_count)
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_metadata_editor_enforces_owner_scope(self) -> None:
        db = FakeMetadataDatabase(roles={8: "admin"})
        callback, message, _ = make_callback("manage:editdetails:42", user_id=8)

        await edit_quest_details_menu(callback, db)

        self.assertFalse(message.edit_text.await_count)
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])
        self.assertEqual(db.updates, [])

    async def test_starting_metadata_edit_cleans_up_superseded_staged_archive_media(self) -> None:
        db = FakeMetadataDatabase()
        bot = FakeBot()
        state = FakeState({
            "stages": [{"source_chat_id": -1001, "source_message_id": 11}],
            "stage_draft": {"source_chat_id": -1002, "source_message_id": 22},
            "cover_chat_id": -1003,
            "cover_message_id": 33,
        })
        callback, _message, _ = make_callback("manage:edit:title:42", bot)

        await begin_quest_title_edit(callback, state, db)

        self.assertEqual(
            bot.deletions,
            [
                {"chat_id": -1001, "message_id": 11},
                {"chat_id": -1002, "message_id": 22},
                {"chat_id": -1003, "message_id": 33},
            ],
        )
        self.assertEqual(state.current_state, EditQuestDetails.title)
        self.assertEqual(state.data["edit_quest_id"], 42)

    async def test_title_edit_validates_and_renders_user_text_as_literal_rich_text(self) -> None:
        db = FakeMetadataDatabase()
        state = FakeState()
        callback, message, _ = make_callback("manage:edit:title:42")
        await begin_quest_title_edit(callback, state, db)
        self.assertEqual(state.current_state, EditQuestDetails.title)
        self.assertEqual(state.data["edit_context"], "quest_metadata")

        invalid = SimpleNamespace(
            from_user=SimpleNamespace(id=7), text="x" * 101, answer=AsyncMock()
        )
        await quest_title_received(invalid, state, db)
        self.assertEqual(db.updates, [])
        self.assertEqual(state.current_state, EditQuestDetails.title)
        self.assertEqual(state.data["edit_quest_id"], 42)

        user_title = "<b>Literal title</b>"
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            text=user_title,
            answer=AsyncMock(),
            answer_rich=AsyncMock(),
        )
        await quest_title_received(message, state, db)
        self.assertEqual(db.quest["title"], user_title)
        self.assertTrue(state.cleared)
        rendered = message.answer_rich.await_args
        rich_message = rendered.args[0]
        self.assertEqual(rich_message.blocks[2].blocks[0].text, user_title)
        self.assertEqual(rich_message.blocks[2].type, "blockquote")
        self.assertIsNotNone(rendered.kwargs["reply_markup"])

    async def test_description_edit_has_separate_validation_and_saves_completed_quest(self) -> None:
        db = FakeMetadataDatabase()
        db.quest["status"] = "completed"
        state = FakeState({"edit_quest_id": 42, "edit_context": "quest_metadata"})
        state.current_state = EditQuestDetails.description

        invalid = SimpleNamespace(
            from_user=SimpleNamespace(id=7), text="d" * 1001, answer=AsyncMock()
        )
        await quest_description_received(invalid, state, db)
        self.assertEqual(db.updates, [])
        self.assertFalse(state.cleared)

        description = "A revised <i>description</i>"
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            text=description,
            answer=AsyncMock(),
            answer_rich=AsyncMock(),
        )
        await quest_description_received(message, state, db)
        self.assertEqual(db.quest["description"], description)
        self.assertTrue(state.cleared)
        rendered = message.answer_rich.await_args
        rich_message = rendered.args[0]
        self.assertEqual(rich_message.blocks[2].blocks[0].text, description)
        self.assertEqual(rich_message.blocks[2].type, "blockquote")

    async def test_cover_editor_copies_existing_archive_reference_and_hides_remove_when_empty(self) -> None:
        db = FakeMetadataDatabase()
        db.quest.update(cover_chat_id=-100200, cover_message_id=17)
        callback, message, bot = make_callback("manage:edit:cover:42")

        await quest_cover_editor(callback, db, bot)

        self.assertEqual(
            bot.copies,
            [{"chat_id": 7, "from_chat_id": -100200, "message_id": 17, "caption": ""}],
        )
        markup = message.edit_text.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "manage:edit:cover:photo:42")
        self.assertEqual(markup.inline_keyboard[1][0].style, "danger")

        db.quest.update(cover_chat_id=None, cover_message_id=None)
        callback, message, _ = make_callback("manage:edit:cover:42")
        await quest_cover_editor(callback, db, bot)
        markup = message.edit_text.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "manage:edit:cover:photo:42")
        self.assertFalse(any("remove:" in button.callback_data for row in markup.inline_keyboard for button in row))

    async def test_cover_editor_embeds_file_id_and_keeps_controls_on_same_rich_message(self) -> None:
        db = FakeMetadataDatabase()
        db.quest.update(
            cover_chat_id=-100200,
            cover_message_id=17,
            cover_file_id="telegram-cover-file-id",
        )
        callback, message, bot = make_callback("manage:edit:cover:42")

        await quest_cover_editor(callback, db, bot)

        self.assertEqual(bot.copies, [])
        rich = message.edit_text.await_args.kwargs["rich_message"]
        self.assertEqual(rich.blocks[2].type, "photo")
        self.assertEqual(rich.blocks[2].photo.media, "telegram-cover-file-id")
        self.assertIsNotNone(message.edit_text.await_args.kwargs["reply_markup"])

    async def test_cover_photo_add_or_replace_archives_telegram_reference_then_removes_old_copy(self) -> None:
        db = FakeMetadataDatabase()
        db.quest.update(cover_chat_id=-100111, cover_message_id=12)
        state = FakeState({"edit_quest_id": 42, "edit_context": "quest_metadata"})
        bot = FakeBot()
        message = make_photo_message(state.data, bot)

        await quest_cover_photo_received(
            message, state, db, SimpleNamespace(question_archive_channel_id=-100999)
        )

        self.assertEqual(
            bot.copies,
            [{"chat_id": -100999, "from_chat_id": 7, "message_id": 105}],
        )
        self.assertEqual(db.quest["cover_chat_id"], -100999)
        self.assertEqual(db.quest["cover_message_id"], 901)
        self.assertEqual(db.quest["cover_file_id"], "telegram-file-id")
        self.assertEqual(db.updates[-1]["cover_file_id"], "telegram-file-id")
        self.assertEqual(bot.deletions, [{"chat_id": -100111, "message_id": 12}])
        self.assertTrue(state.cleared)
        self.assertIsNotNone(message.answer_rich.await_args.kwargs["reply_markup"])

    async def test_cover_add_without_previous_photo_does_not_download_or_delete_media(self) -> None:
        db = FakeMetadataDatabase()
        state = FakeState({"edit_quest_id": 42, "edit_context": "quest_metadata"})
        bot = FakeBot()
        message = make_photo_message(state.data, bot)

        await quest_cover_photo_received(
            message, state, db, SimpleNamespace(question_archive_channel_id=-100999)
        )

        self.assertEqual(db.quest["cover_chat_id"], -100999)
        self.assertEqual(db.quest["cover_message_id"], 901)
        self.assertEqual(db.quest["cover_file_id"], "telegram-file-id")
        self.assertEqual(len(bot.copies), 1)
        self.assertEqual(bot.deletions, [])

    async def test_cover_photo_is_not_saved_when_quest_was_archived_mid_flow(self) -> None:
        class ArchivedDuringCopyDatabase(FakeMetadataDatabase):
            async def update_quest_metadata(self, quest_id, changes, actor_id, now=None):
                self.quest["status"] = "archived"

        db = ArchivedDuringCopyDatabase()
        state = FakeState({"edit_quest_id": 42, "edit_context": "quest_metadata"})
        bot = FakeBot()
        message = make_photo_message(state.data, bot)

        await quest_cover_photo_received(
            message, state, db, SimpleNamespace(question_archive_channel_id=-100999)
        )

        self.assertEqual(bot.copies[0]["chat_id"], -100999)
        self.assertEqual(bot.deletions, [{"chat_id": -100999, "message_id": 901}])
        self.assertTrue(state.cleared)
        self.assertEqual(db.quest.get("cover_message_id"), None)

    async def test_cover_removal_requires_confirmation_and_deletes_archived_message_reference(self) -> None:
        db = FakeMetadataDatabase()
        db.quest.update(
            cover_chat_id=-100333,
            cover_message_id=81,
            cover_file_id="telegram-cover-file-id",
        )
        callback, message, bot = make_callback("manage:edit:cover:remove:42")

        await confirm_quest_cover_removal_prompt(callback, db)

        confirm_markup = message.edit_text.await_args.kwargs["reply_markup"]
        self.assertEqual(confirm_markup.inline_keyboard[0][0].style, "danger")
        self.assertTrue(any("remove-confirm:42" in button.callback_data for row in confirm_markup.inline_keyboard for button in row))
        self.assertEqual(db.quest["cover_message_id"], 81)

        callback, _message, bot = make_callback("manage:edit:cover:remove-confirm:42", bot)
        await remove_quest_cover(callback, db)
        self.assertIsNone(db.quest["cover_chat_id"])
        self.assertIsNone(db.quest["cover_message_id"])
        self.assertIsNone(db.quest["cover_file_id"])
        self.assertEqual(bot.deletions, [{"chat_id": -100333, "message_id": 81}])
        self.assertEqual(
            db.updates[-1],
            {
                "cover_chat_id": None,
                "cover_message_id": None,
                "cover_file_id": None,
            },
        )

    async def test_cover_photo_action_and_removal_are_disabled_after_archive(self) -> None:
        db = FakeMetadataDatabase()
        db.quest.update(status="archived", cover_chat_id=-1001, cover_message_id=8)
        callback, _message, bot = make_callback("manage:edit:cover:photo:42")
        state = FakeState()
        await begin_quest_cover_photo_edit(callback, state, db)
        self.assertIsNone(state.current_state)
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

        callback, _message, bot = make_callback("manage:edit:cover:remove-confirm:42", bot)
        await remove_quest_cover(callback, db)
        self.assertEqual(db.quest["cover_message_id"], 8)
        self.assertEqual(bot.deletions, [])
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_cancel_returns_to_metadata_menu_without_changing_question_edit_rules(self) -> None:
        db = FakeMetadataDatabase()
        state = FakeState({"edit_quest_id": 42, "edit_context": "quest_metadata"})
        callback, message, _ = make_callback("manage:editcancel")

        await cancel_stage_edit(callback, state, db)

        self.assertTrue(state.cleared)
        self.assertIsNotNone(message.answer.await_args.kwargs["reply_markup"])
        self.assertEqual(
            message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data,
            "manage:edit:title:42",
        )


class QuestMetadataKeyboardTests(unittest.TestCase):
    def test_native_button_styles_are_semantic_and_cover_actions_are_distinct(self) -> None:
        details = edit_quest_details_keyboard("en", 42)
        self.assertEqual(details.inline_keyboard[0][0].style, "primary")
        self.assertEqual(details.inline_keyboard[0][0].callback_data, "manage:edit:title:42")

        with_cover = edit_quest_cover_keyboard("en", 42, True)
        self.assertEqual(with_cover.inline_keyboard[0][0].style, "primary")
        self.assertEqual(with_cover.inline_keyboard[1][0].style, "danger")
        without_cover = edit_quest_cover_keyboard("en", 42, False)
        self.assertEqual(len(without_cover.inline_keyboard), 3)

        confirmation = confirm_cover_removal_keyboard("en", 42)
        self.assertEqual(confirmation.inline_keyboard[0][0].style, "danger")
        self.assertEqual(confirmation.inline_keyboard[0][1].style, "primary")

    def test_native_button_style_defaults_follow_callback_semantics(self) -> None:
        self.assertEqual(button("archive", "manage:archive:42").style, "danger")
        self.assertEqual(button("join", "quest:joinconfirm:42").style, "success")
        self.assertEqual(button("browse", "browse:filter:all:0").style, "primary")
        join = join_confirmation_keyboard("en", 42)
        self.assertEqual(join.inline_keyboard[0][0].style, "success")
        self.assertEqual(join.inline_keyboard[0][1].style, "danger")

    def test_completed_quests_keep_metadata_edit_but_not_question_edit(self) -> None:
        quest = {
            "id": 42,
            "status": "completed",
            "visibility": "public",
            "paused_at": None,
        }
        markup = manage_quest("en", quest, "admin")
        callback_data = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("manage:editdetails:42", callback_data)
        self.assertNotIn("manage:editquestions:42", callback_data)

    def test_all_button_labels_and_new_metadata_messages_have_three_localizations(self) -> None:
        for key, translations in TEXTS.items():
            if key.startswith("btn_"):
                self.assertEqual(set(translations), set(LANGUAGES), key)
                for language in LANGUAGES:
                    self.assertTrue(translations[language][0], (key, language))
        for key in (
            "btn_edit_details",
            "btn_edit_title",
            "btn_edit_description",
            "btn_edit_cover",
            "btn_replace_cover",
            "btn_remove_cover",
            "btn_confirm_remove_cover",
            "ask_quest_title",
            "ask_quest_description",
            "ask_quest_cover",
            "confirm_remove_cover",
            "quest_title_updated",
            "quest_description_updated",
            "quest_cover_updated",
            "quest_cover_removed",
        ):
            self.assertEqual(set(TEXTS[key]), set(LANGUAGES), key)
