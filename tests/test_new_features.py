from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import AsyncMock

from quest_bot import navigation
from quest_bot.database import Database, utc_now
from quest_bot.handlers.creation import (
    answer_mode_selected,
    attempts_mode_different,
    attempts_mode_same,
    common_attempts_received,
)
from quest_bot.handlers.quests import (
    _leaderboard_back_target,
    _parse_quest_card,
    _process_answer,
    aggregate_leaderboard,
    leaderboard_quest_list,
    manage_quest_callback,
    participant_answer,
    ratings_overview,
    show_leaderboard,
    view_quest_callback,
)
from quest_bot.keyboards import (
    admin_detail_keyboard,
    admin_list_keyboard,
    admin_panel,
    browse_filters,
    confirm_purge_quest_keyboard,
    deleted_quest_keyboard,
    guide_keyboard,
    language_keyboard,
    main_menu,
    managed_chats_keyboard,
    manage_chat_keyboard,
    open_quest_keyboard,
    quest_detail,
    review_keyboard,
    ticket_reply_keyboard,
)
from quest_bot.handlers.admin import (
    admin_message_received,
    confirm_quest_purge,
    purge_quest_confirmed,
    remove_whitelist,
    show_admin_detail,
    show_admin_quests,
    show_admins_page,
    show_chat,
    show_logs,
    show_managed_chats_page,
)
from quest_bot.handlers.creation import duration_received, start_time_received
from quest_bot.handlers.quests import quest_duration_received
from quest_bot.handlers.support import (
    list_my_tickets_page,
    open_support_page,
    pending_text_as_ticket,
    show_ticket_history,
)
from quest_bot.localization import LANGUAGES, TEXTS, yangi_uzbek
from quest_bot.states import CreateQuest
from quest_bot.services import answer_button
from quest_bot.utils import rank_label


def setUpModule() -> None:
    """Navigation breadcrumbs are process-wide, so start every module clean."""
    navigation.reset()


class FakeState:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})
        self.current_state = None

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, **values) -> None:
        self.data.update(values)

    async def set_state(self, state) -> None:
        self.current_state = state

    async def get_state(self):
        return self.current_state

    async def clear(self) -> None:
        self.data = {}


class LanguageTests(unittest.TestCase):
    def test_every_text_has_all_five_languages(self) -> None:
        self.assertEqual(LANGUAGES, ("uz", "uzn", "kaa", "ru", "en"))
        for key, translations in TEXTS.items():
            with self.subTest(key=key):
                self.assertEqual(set(translations), set(LANGUAGES))
                for language in LANGUAGES:
                    self.assertTrue(translations[language].strip())

    def test_new_uzbek_changes_only_the_four_letters(self) -> None:
        self.assertEqual(
            yangi_uzbek("o‘zbek g‘alaba shahar choy"),
            "özbek ğalaba şahar çoy",
        )
        for key, translations in TEXTS.items():
            with self.subTest(key=key):
                self.assertEqual(translations["uzn"], yangi_uzbek(translations["uz"]))

    def test_new_uzbek_keeps_the_format_placeholders(self) -> None:
        placeholder = re.compile(r"\{[^{}]*\}")
        for key, translations in TEXTS.items():
            with self.subTest(key=key):
                self.assertEqual(
                    sorted(placeholder.findall(translations["uzn"])),
                    sorted(placeholder.findall(translations["uz"])),
                )
        self.assertIn("{chat_id}", TEXTS["chat_id_result"]["uzn"])
        self.assertIn("{chat}", TEXTS["quest_details"]["uzn"])

    def test_karakalpak_is_a_full_translation(self) -> None:
        translated = sum(
            1
            for translations in TEXTS.values()
            if translations["kaa"] != translations["uz"]
        )
        self.assertGreater(translated / len(TEXTS), 0.9)
        self.assertEqual(TEXTS["menu"]["kaa"], "Bas menyu")
        self.assertNotIn("Asosiy menyu", TEXTS["menu"]["kaa"])

    def test_language_keyboard_offers_every_language(self) -> None:
        buttons = language_keyboard()
        self.assertEqual(
            [row[0].callback_data for row in buttons.inline_keyboard],
            [f"lang:{language}" for language in LANGUAGES],
        )


class MenuAndCardKeyboardTests(unittest.TestCase):
    def _quest(self, **overrides) -> dict:
        quest = {
            "id": 17,
            "owner_id": 90,
            "title": "Night Quest",
            "visibility": "public",
            "status": "active",
            "paused_at": None,
            "chat_id": None,
        }
        quest.update(overrides)
        return quest

    def test_managed_quests_live_in_the_admin_panel_only(self) -> None:
        menu = main_menu("en", "admin")
        callback_data = [
            button.callback_data for row in menu.inline_keyboard for button in row
        ]
        self.assertNotIn("adminq:filter:all:0", callback_data)
        self.assertIn("admin:home", callback_data)

        panel = admin_panel("en", "admin")
        panel_data = [
            button.callback_data for row in panel.inline_keyboard for button in row
        ]
        self.assertIn("adminq:filter:all:0", panel_data)

    def test_quest_card_keeps_the_start_button_below_the_rating_button(self) -> None:
        start_card = quest_detail("en", self._quest(), joined=True, can_continue=True)
        self.assertEqual(
            start_card.inline_keyboard[0][0].callback_data,
            "rating:show:17:card:browse:all:0",
        )
        self.assertEqual(start_card.inline_keyboard[1][0].callback_data, "quest:continue:17")
        self.assertEqual(start_card.inline_keyboard[1][0].text, TEXTS["btn_start_quest"]["en"])

        continue_card = quest_detail(
            "en", self._quest(), joined=True, can_continue=True, continue_started=True
        )
        self.assertEqual(
            continue_card.inline_keyboard[1][0].text, TEXTS["btn_continue_quest"]["en"]
        )

    def test_rating_button_remembers_where_the_card_was_opened(self) -> None:
        card = quest_detail(
            "en",
            self._quest(),
            joined=True,
            from_my_quests=True,
            my_quests_visibility="private",
            my_quests_page=2,
        )
        self.assertEqual(
            card.inline_keyboard[0][0].callback_data,
            "rating:show:17:card:my:private:2",
        )


class DatabaseFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        navigation.reset()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "features.sqlite3")
        await self.db.initialize()
        await self.db.seed_superadmins([1])
        await self.db.ensure_user(20, "player", "Test Player")
        self.start_at = "2026-10-01T00:00:00+00:00"

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def create_quest(
        self, progression: str = "immediate", answer_mode: str = "auto"
    ) -> int:
        self._quest_seq = getattr(self, "_quest_seq", 0) + 1
        quest = {
            "title": "Scheduled quest",
            "description": "Description",
            "visibility": "public",
            "progression": progression,
            "start_at": self.start_at,
            "duration_seconds": 0,
            "chat_id": None,
            "invite_token": f"feature-token-{self._quest_seq}",
        }
        stages = [
            {
                "question": "First question",
                "answer_mode": answer_mode,
                "correct_answer": "Exact" if answer_mode == "auto" else None,
                "max_attempts": 2,
                "time_limit_seconds": 0,
                "starts_at": self.start_at,
            },
            {
                "question": "Second question",
                "answer_mode": "auto",
                "correct_answer": "Final",
                "max_attempts": 1,
                "time_limit_seconds": 0,
                "starts_at": "2026-10-01T01:00:00+00:00",
            },
        ]
        return await self.db.create_quest(1, quest, stages)

    async def test_soft_delete_hides_the_quest_and_restore_brings_it_back(self) -> None:
        quest_id = await self.create_quest(progression="scheduled")

        self.assertTrue(await self.db.soft_delete_quest(quest_id, 1, utc_now()))
        self.assertIsNone(await self.db.get_quest(quest_id))
        hidden = await self.db.get_quest(quest_id, include_deleted=True)
        self.assertEqual(int(hidden["deleted"]), 1)
        self.assertEqual(await self.db.list_public_quests(None, 0, 10), [])
        managed = await self.db.list_manageable_quests(1, True, None, 0, 10, deleted=True)
        self.assertEqual([quest["id"] for quest in managed], [quest_id])
        self.assertEqual(
            await self.db.list_manageable_quests(1, True, None, 0, 10), []
        )
        # The question itself is still stored: nothing was physically removed.
        self.assertIsNotNone(await self.db.get_stage(quest_id, 1))

        self.assertTrue(await self.db.restore_quest(quest_id, 1, utc_now()))
        self.assertIsNotNone(await self.db.get_quest(quest_id))
        self.assertEqual(
            [quest["id"] for quest in await self.db.list_public_quests(None, 0, 10)],
            [quest_id],
        )
        self.assertFalse(await self.db.soft_delete_quest(quest_id + 99, 1, utc_now()))

    async def test_purge_removes_a_deleted_quest_and_all_its_data(self) -> None:
        quest_id = await self.create_quest()
        await self.db.set_quest_status(quest_id, "active")
        joined = await self.db.join_quest(quest_id, 20, None, utc_now())
        self.assertEqual(joined["code"], "joined")
        stage = await self.db.get_stage(quest_id, 1)
        await self.db.activate_stage_for_participant(quest_id, 20, stage, utc_now())
        await self.db.mark_stage_delivered(quest_id, 20, int(stage["id"]), utc_now())
        await self.db.submit_answer(quest_id, 20, "Exact", utc_now())

        # A live quest can never be erased by accident.
        self.assertFalse(await self.db.purge_quest(quest_id, 1))
        self.assertIsNotNone(await self.db.get_quest(quest_id))

        self.assertTrue(await self.db.soft_delete_quest(quest_id, 1, utc_now()))
        self.assertTrue(await self.db.purge_quest(quest_id, 1))

        self.assertIsNone(await self.db.get_quest(quest_id, include_deleted=True))
        self.assertIsNone(await self.db.get_stage(quest_id, 1))
        self.assertIsNone(await self.db.participant(quest_id, 20))
        self.assertEqual(
            await self.db.list_manageable_quests(1, True, None, 0, 10, deleted=True), []
        )
        # The deletion itself stays in the audit log.
        actions = [row["action"] for row in await self.db.latest_logs(20)]
        self.assertIn("quest.purged", actions)
        self.assertFalse(await self.db.purge_quest(quest_id, 1))

    async def test_a_late_joiner_can_still_join_and_play(self) -> None:
        quest_id = await self.create_quest()
        await self.db.set_quest_status(quest_id, "active")

        # Every stage start time is long past: the quest is running, not over.
        joined = await self.db.join_quest(quest_id, 20, None, utc_now())
        self.assertEqual(joined["code"], "joined")
        self.assertEqual(
            (await self.db.participant(quest_id, 20))["status"], "active"
        )
        stage = await self.db.next_deliverable_stage(quest_id, 20, utc_now())
        self.assertEqual(stage["stage_order"], 1)
        self.assertTrue(
            await self.db.activate_stage_for_participant(quest_id, 20, stage, utc_now())
        )
        self.assertTrue(
            await self.db.mark_stage_delivered(
                quest_id, 20, int(stage["id"]), utc_now()
            )
        )
        self.assertEqual(len(await self.db.open_stages_for_user(20)), 1)

        # A scheduled quest hands a late joiner the stage that is due right now.
        scheduled_id = await self.create_quest(progression="scheduled")
        await self.db.set_quest_status(scheduled_id, "active")
        joined_late = await self.db.join_quest(scheduled_id, 20, None, utc_now())
        self.assertEqual(joined_late["code"], "joined")
        due = await self.db.next_deliverable_stage(scheduled_id, 20, utc_now())
        self.assertEqual(due["stage_order"], 2)

    async def test_joining_closes_once_the_quest_is_finished(self) -> None:
        quest_id = await self.create_quest()
        await self.db.set_quest_status(quest_id, "active")
        await self.db.ensure_user(21, "late", "Late Player")
        self.assertEqual(
            (await self.db.join_quest(quest_id, 21, None, utc_now()))["code"], "joined"
        )
        await self.db.set_quest_status(quest_id, "completed")
        await self.db.ensure_user(22, "later", "Later Player")
        self.assertEqual(
            (await self.db.join_quest(quest_id, 22, None, utc_now()))["code"], "closed"
        )

    async def test_paged_button_lists_share_one_page_size_setting(self) -> None:
        await self.db.add_admin(30, 1)
        await self.db.add_admin(31, 1)
        admins, total = await self.db.list_admins_page(0, 2)
        self.assertEqual(total, 3)
        self.assertEqual(len(admins), 2)
        second, total = await self.db.list_admins_page(1, 2)
        self.assertEqual(total, 3)
        self.assertEqual([admin["telegram_id"] for admin in second], [31])
        # An out-of-range page is clamped to the last one.
        clamped, _ = await self.db.list_admins_page(99, 2)
        self.assertEqual([admin["telegram_id"] for admin in clamped], [31])

        for index in range(3):
            await self.db.create_ticket(20, None, None, f"message {index}")
        tickets, ticket_total, page = await self.db.user_tickets_page(20, 1, 2)
        self.assertEqual(ticket_total, 3)
        self.assertEqual(page, 1)
        self.assertEqual(len(tickets), 1)

        await self.db.register_chat(-1001, "Group A", "supergroup", 1)
        await self.db.register_chat(-1002, "Group B", "supergroup", 1)
        chats, chat_total, chat_page = await self.db.managed_chats_page(0, 1)
        self.assertEqual((chat_total, chat_page, len(chats)), (2, 0, 1))

        await self.db.add_chat_whitelist(-1001, 20, 1)
        await self.db.add_chat_whitelist(-1001, 21, 1)
        self.assertEqual(await self.db.chat_whitelist_count(-1001), 2)
        first_page = await self.db.chat_whitelist(-1001, 0, 1)
        self.assertEqual([item["user_id"] for item in first_page], [20])
        self.assertEqual(await self.db.chat_whitelist_count(-1002), 0)

        quest_id = await self.create_quest()
        await self.db.set_quest_status(quest_id, "active")
        await self.db.join_quest(quest_id, 20, None, utc_now())
        self.assertEqual(await self.db.support_quest_count(20), 1)
        self.assertEqual(
            (await self.db.support_quest_for_user(quest_id, 20))["owner_id"], 1
        )
        self.assertIsNone(await self.db.support_quest_for_user(quest_id, 21))
        page = await self.db.support_quests_for_user(20, 0, 10)
        self.assertEqual([item["id"] for item in page], [quest_id])

    async def test_new_start_time_shifts_the_scheduled_stages(self) -> None:
        quest_id = await self.create_quest(progression="scheduled")

        previous = await self.db.update_quest_start_at(
            quest_id, "2026-10-01T02:00:00+00:00", 1, utc_now()
        )
        self.assertEqual(previous["start_at"], self.start_at)
        quest = await self.db.get_quest(quest_id)
        self.assertEqual(quest["start_at"], "2026-10-01T02:00:00+00:00")
        self.assertEqual(
            (await self.db.get_stage(quest_id, 1))["starts_at"],
            "2026-10-01T02:00:00+00:00",
        )
        self.assertEqual(
            (await self.db.get_stage(quest_id, 2))["starts_at"],
            "2026-10-01T03:00:00+00:00",
        )

        await self.db.set_quest_status(quest_id, "active")
        self.assertIsNone(
            await self.db.update_quest_start_at(
                quest_id, "2026-10-01T05:00:00+00:00", 1, utc_now()
            )
        )

    async def test_a_question_must_be_delivered_before_it_is_offered(self) -> None:
        quest_id = await self.create_quest()
        await self.db.set_quest_status(quest_id, "active")
        joined = await self.db.join_quest(quest_id, 20, None, utc_now())
        self.assertEqual(joined["code"], "joined")
        now = utc_now()
        stage = await self.db.get_stage(quest_id, 1)

        self.assertTrue(
            await self.db.activate_stage_for_participant(quest_id, 20, stage, now)
        )
        self.assertEqual(await self.db.open_stages_for_user(20), [])
        deliverable = await self.db.next_deliverable_stage(quest_id, 20, now)
        self.assertEqual(deliverable["stage_order"], 1)

        self.assertTrue(
            await self.db.mark_stage_delivered(quest_id, 20, int(stage["id"]), now)
        )
        open_stages = await self.db.open_stages_for_user(20)
        self.assertEqual([item["quest_id"] for item in open_stages], [quest_id])
        self.assertEqual(open_stages[0]["stage_order"], 1)

        answered = await self.db.submit_answer(quest_id, 20, "Exact", utc_now())
        self.assertEqual(answered["code"], "correct")
        self.assertEqual(answered["next_stage_order"], 2)
        next_stage = await self.db.next_deliverable_stage(quest_id, 20, utc_now())
        self.assertEqual(next_stage["stage_order"], 2)

    async def test_pending_review_hides_the_next_question(self) -> None:
        quest_id = await self.create_quest(answer_mode="manual")
        await self.db.set_quest_status(quest_id, "active")
        await self.db.join_quest(quest_id, 20, None, utc_now())
        now = utc_now()
        stage = await self.db.get_stage(quest_id, 1)
        await self.db.activate_stage_for_participant(quest_id, 20, stage, now)
        await self.db.mark_stage_delivered(quest_id, 20, int(stage["id"]), now)

        submitted = await self.db.submit_answer(quest_id, 20, "manual answer", utc_now())
        self.assertEqual(submitted["code"], "pending")
        self.assertIsNone(await self.db.next_deliverable_stage(quest_id, 20, utc_now()))
        self.assertEqual(await self.db.open_stages_for_user(20), [])

    async def test_language_choices_include_the_two_new_languages(self) -> None:
        await self.db.set_language(20, "kaa")
        self.assertEqual(await self.db.get_language(20), "kaa")
        await self.db.set_language(20, "uzn")
        self.assertEqual(await self.db.get_language(20), "uzn")
        with self.assertRaises(ValueError):
            await self.db.set_language(20, "de")


class CreationAttemptsPolicyTests(unittest.IsolatedAsyncioTestCase):
    def _db(self):
        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

        return Db()

    def _callback(self, data: str):
        return SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=90),
            message=SimpleNamespace(answer=AsyncMock()),
            answer=AsyncMock(),
        )

    async def test_shared_limit_is_asked_once_for_every_stage(self) -> None:
        state = FakeState()
        callback = self._callback("create:attempts:same")
        await attempts_mode_same(callback, state, self._db())
        self.assertEqual(state.current_state, CreateQuest.common_attempts)

        message = SimpleNamespace(
            from_user=SimpleNamespace(id=90),
            text="3",
            answer=AsyncMock(),
        )
        await common_attempts_received(message, state, self._db())
        self.assertEqual(state.current_state, CreateQuest.question)
        self.assertEqual(state.data["common_max_attempts"], 3)

        # The per-stage question is skipped: the shared value is reused.
        manual = SimpleNamespace(
            data="create:answer:manual",
            from_user=SimpleNamespace(id=90),
            message=SimpleNamespace(answer=AsyncMock()),
            answer=AsyncMock(),
        )
        await answer_mode_selected(manual, state, self._db())
        self.assertEqual(state.current_state, CreateQuest.stage_time)
        self.assertEqual(state.data["stage_draft"]["max_attempts"], 3)

    async def test_per_stage_limits_start_the_first_question(self) -> None:
        state = FakeState()
        callback = self._callback("create:attempts:different")
        await attempts_mode_different(callback, state, self._db())
        self.assertEqual(state.current_state, CreateQuest.question)


class LeaderboardBackButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_leaderboard_back_button_returns_to_its_origin(self) -> None:
        class Db:
            def __init__(self, manager: bool) -> None:
                self.manager = manager

            async def get_language(self, user_id: int) -> str:
                return "en"

            async def get_quest(self, quest_id: int) -> dict:
                return {
                    "id": quest_id,
                    "owner_id": 9 if self.manager else 1,
                    "visibility": "public",
                    "title": "Night Quest",
                }

            async def participant(self, quest_id: int, user_id: int):
                return None

            async def leaderboard(self, quest_id: int):
                return [
                    {
                        "user_id": 20,
                        "full_name": "First Player",
                        "status": "completed",
                        "solved": 3,
                        "completed_at": "2026-10-01T10:00:00+00:00",
                    },
                    {
                        "user_id": 21,
                        "full_name": "Second Player",
                        "status": "active",
                        "solved": 2,
                        "completed_at": None,
                    },
                    {
                        "user_id": 22,
                        "full_name": "Sixth Player",
                        "status": "failed",
                        "solved": 1,
                        "completed_at": None,
                    },
                ]

            async def get_role(self, user_id: int) -> str:
                return "admin" if self.manager else "user"

        async def render(origin: str, manager: bool):
            message = SimpleNamespace(edit_text=AsyncMock())
            callback = SimpleNamespace(
                data=f"rating:show:17:{origin}",
                from_user=SimpleNamespace(id=9),
                message=message,
                answer=AsyncMock(),
            )
            await show_leaderboard(callback, Db(manager))
            return message

        # A rating opened from a quest card returns to that card, not to the
        # participating-quest list, and offers a way back to the quest as well.
        def markup(message):
            return message.edit_text.await_args.kwargs["reply_markup"]

        def text(message):
            return json.dumps(
                message.edit_text.await_args.kwargs["rich_message"].model_dump(
                    mode="json"
                ),
                ensure_ascii=False,
            )

        # Back alone returns to the quest card, and the main menu closes the
        # screen; no separate Open-quest button is needed.
        joined = await render("card:my:private:1", manager=False)
        self.assertEqual(
            markup(joined).inline_keyboard[0][0].callback_data,
            "quest:view:my:private:1:17",
        )
        self.assertEqual(
            markup(joined).inline_keyboard[1][0].callback_data, "menu:home"
        )

        browsed = await render("card:browse", manager=False)
        self.assertEqual(markup(browsed).inline_keyboard[0][0].callback_data, "quest:view:17")

        managed = await render("manage", manager=True)
        self.assertEqual(markup(managed).inline_keyboard[0][0].callback_data, "manage:quest:17")
        self.assertEqual(markup(managed).inline_keyboard[1][0].callback_data, "admin:home")

        listed = await render("list:managed:2", manager=True)
        self.assertEqual(
            markup(listed).inline_keyboard[0][0].callback_data, "ratings:list:managed:2"
        )

        legacy = await render("my:private:1", manager=False)
        self.assertEqual(
            markup(legacy).inline_keyboard[0][0].callback_data, "quest:mylist:private:1"
        )

        # The first five ranks carry medals, later ones stay plain numbers.
        rendered = text(joined)
        self.assertIn("🥇 1.", rendered)
        self.assertIn("🥈 2.", rendered)
        self.assertIn("🥉 3.", rendered)


class SupportPendingTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_text_can_be_sent_as_a_support_ticket(self) -> None:
        recorded: dict = {}

        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def get_ticket(self, ticket_id: int) -> dict:
                return {"id": ticket_id, "user_id": 20, "target_admin_id": None}

            async def get_user(self, user_id: int) -> dict:
                return {"full_name": "Player"}

            async def support_admin_recipients(self, ticket_id: int):
                return []

            async def create_ticket(self, user_id, quest_id, admin_id, text):
                recorded.update(
                    user_id=user_id, quest_id=quest_id, admin_id=admin_id, text=text
                )
                return 55

            async def log_action(self, *args):
                recorded["log"] = args

        state = FakeState(
            {
                "support_quest_id": 3,
                "support_target_admin_id": 9,
                "pending_support_text": "Should this be a ticket?",
            }
        )
        callback = SimpleNamespace(
            data="support:pending:ticket",
            from_user=SimpleNamespace(id=20),
            message=SimpleNamespace(
                chat=SimpleNamespace(type="private"), answer=AsyncMock()
            ),
            answer=AsyncMock(),
        )

        await pending_text_as_ticket(callback, state, Db())

        self.assertEqual(recorded["user_id"], 20)
        self.assertEqual(recorded["quest_id"], 3)
        self.assertEqual(recorded["admin_id"], 9)
        self.assertEqual(recorded["text"], "Should this be a ticket?")
        self.assertEqual(recorded["log"][1], "support.ticket.created")
        self.assertEqual(state.data, {})
        self.assertEqual(
            callback.message.answer.await_args.args[0], TEXTS["support_created"]["en"]
        )


class AnswerRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_without_an_open_stage_offers_the_live_quest(self) -> None:
        class Db:
            def __init__(self, quest_id: int | None, status: str = "active") -> None:
                self.quest_id = quest_id
                self.status = status

            async def open_stages_for_user(self, user_id: int):
                return []

            async def get_language(self, user_id: int) -> str:
                return "en"

            async def latest_live_quest_id(self, user_id: int):
                return self.quest_id

            async def get_quest(self, quest_id: int) -> dict:
                return {"id": quest_id, "status": self.status}

        async def send(db: Db):
            message = SimpleNamespace(
                from_user=SimpleNamespace(id=20),
                text="maybe an answer",
                answer=AsyncMock(),
            )
            await participant_answer(message, FakeState(), db, SimpleNamespace())
            return message

        active = await send(Db(17))
        self.assertIn(
            TEXTS["btn_continue_quest"]["en"], active.answer.await_args.args[0]
        )
        markup = active.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "quest:continue:17")

        waiting = await send(Db(18, "scheduled"))
        self.assertEqual(
            waiting.answer.await_args.args[0],
            TEXTS["quest_started_waiting"]["en"],
        )
        self.assertIsNone(waiting.answer.await_args.kwargs["reply_markup"])

        silent = await send(Db(None))
        self.assertFalse(silent.answer.await_count)


class RankMedalTests(unittest.TestCase):
    def test_only_the_first_five_ranks_get_a_medal(self) -> None:
        self.assertEqual(
            [rank_label(rank) for rank in range(1, 7)],
            ["🥇 1", "🥈 2", "🥉 3", "🏅 4", "🎖 5", "6"],
        )


class QuestPageButtonTests(unittest.TestCase):
    def test_participant_notices_link_back_to_the_quest(self) -> None:
        markup = open_quest_keyboard("en", 17)
        self.assertEqual(
            markup.inline_keyboard[0][0].callback_data, "quest:view:my:all:0:17"
        )
        self.assertEqual(
            markup.inline_keyboard[0][0].text, TEXTS["btn_open_quest"]["en"]
        )

    def test_deleted_quest_offers_restore_and_permanent_deletion(self) -> None:
        markup = deleted_quest_keyboard("en", 17)
        self.assertEqual(
            markup.inline_keyboard[0][0].callback_data, "manage:restore:17"
        )
        self.assertEqual(
            markup.inline_keyboard[1][0].callback_data, "manage:purge:17"
        )

        confirm = confirm_purge_quest_keyboard("en", 17)
        self.assertEqual(
            confirm.inline_keyboard[0][0].callback_data, "manage:purgeconfirm:17"
        )
        self.assertEqual(confirm.inline_keyboard[1][0].callback_data, "manage:quest:17")

    def test_join_confirmation_promises_a_start_notice(self) -> None:
        self.assertIn("xabar beriladi", TEXTS["join_success"]["uz"])
        self.assertIn("notified", TEXTS["join_success"]["en"])
        self.assertNotIn("Savollar va javob natijalari", TEXTS["join_success"]["uz"])


class AdminOverviewTests(unittest.IsolatedAsyncioTestCase):
    def test_admin_list_has_message_and_detail_buttons(self) -> None:
        admins = [
            {"telegram_id": 5, "role": "admin", "full_name": "Quest Admin"},
            {"telegram_id": 1, "role": "superadmin", "full_name": "Chief"},
        ]
        rows = admin_list_keyboard("en", admins).inline_keyboard
        self.assertEqual(rows[0][0].callback_data, "super:admin:5")
        self.assertEqual(rows[0][1].callback_data, "super:adminmsg:5")
        self.assertEqual(rows[1][0].callback_data, "super:admin:1")
        self.assertEqual(rows[1][1].callback_data, "super:adminmsg:1")

        admin = {"telegram_id": 5, "role": "admin", "full_name": "Quest Admin"}
        detail = admin_detail_keyboard("en", admin, 3).inline_keyboard
        self.assertEqual(detail[0][0].callback_data, "super:adminquests:5:0")
        self.assertEqual(detail[1][0].callback_data, "super:adminmsg:5")
        self.assertEqual(detail[2][0].callback_data, "super:removeadmin:5")
        superadmin = {"telegram_id": 1, "role": "superadmin", "full_name": "Chief"}
        codes = [
            row[0].callback_data
            for row in admin_detail_keyboard("en", superadmin, 0).inline_keyboard
        ]
        self.assertNotIn("super:removeadmin:1", codes)

    async def test_admin_quest_list_marks_deleted_quests(self) -> None:
        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def get_role(self, user_id: int) -> str:
                return "superadmin"

            async def settings_get(self, key: str, default: str) -> str:
                return "10"

            async def list_admins(self):
                return [
                    {"telegram_id": 5, "role": "admin", "full_name": "Quest Admin"}
                ]

            async def list_owner_quests(self, owner_id: int, include_deleted=False):
                return [
                    {"id": 17, "title": "Live quest", "status": "active", "deleted": 0},
                    {"id": 18, "title": "Gone quest", "status": "completed", "deleted": 1},
                ]

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"), edit_text=AsyncMock()
        )
        callback = SimpleNamespace(
            data="super:adminquests:5:0",
            from_user=SimpleNamespace(id=1),
            message=message,
            answer=AsyncMock(),
        )

        await show_admin_quests(callback, Db())

        payload = json.dumps(
            message.edit_text.await_args.kwargs["rich_message"].model_dump(mode="json"),
            ensure_ascii=False,
        )
        self.assertIn("Live quest", payload)
        self.assertIn("Gone quest", payload)
        self.assertIn("🗑", payload)
        markup = message.edit_text.await_args.kwargs["reply_markup"]
        self.assertEqual(
            [button.callback_data for row in markup.inline_keyboard for button in row],
            [
                "manage:quest:17:super:adminquests:5:0",
                "manage:quest:18:super:adminquests:5:0",
                "super:admin:5",
                "admin:home",
            ],
        )

    async def test_superadmin_message_reaches_the_admin(self) -> None:
        recorded: dict = {}

        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def get_role(self, user_id: int) -> str:
                return "superadmin"

            async def list_admins(self):
                return [
                    {"telegram_id": 5, "role": "admin", "full_name": "Quest Admin"}
                ]

            async def log_action(self, *args):
                recorded["log"] = args

        bot = SimpleNamespace(send_message=AsyncMock())
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            text="Please review your quests today.",
            bot=bot,
            answer=AsyncMock(),
        )
        state = FakeState({"admin_message_to": 5})

        await admin_message_received(message, state, Db())

        self.assertEqual(bot.send_message.await_args.args[0], 5)
        self.assertIn("Please review your quests today.", bot.send_message.await_args.args[1])
        self.assertEqual(recorded["log"][1], "admin.message.sent")
        self.assertEqual(recorded["log"][3], 5)
        self.assertEqual(state.data, {})
        self.assertIn("Quest Admin", message.answer.await_args.args[0])


class TerminalLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_log_renders_as_a_terminal_block(self) -> None:
        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def get_role(self, user_id: int) -> str:
                return "superadmin"

            async def latest_logs(self, limit: int = 20):
                return [
                    {
                        "created_at": "2026-10-04T09:30:00+00:00",
                        "action": "quest.purged",
                        "entity_type": "quest",
                        "entity_id": "17",
                        "actor_id": 1,
                    },
                    {
                        "created_at": "2026-10-04T09:29:00+00:00",
                        "action": "participant.joined",
                        "entity_type": "quest",
                        "entity_id": "17",
                        "actor_id": None,
                    },
                ]

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"), edit_text=AsyncMock()
        )
        callback = SimpleNamespace(
            data="super:logs",
            from_user=SimpleNamespace(id=1),
            message=message,
            answer=AsyncMock(),
        )

        await show_logs(callback, Db())

        rich = message.edit_text.await_args.kwargs["rich_message"]
        blocks = rich.model_dump(mode="json")["blocks"]
        pre = [block for block in blocks if block["type"] == "pre"]
        self.assertEqual(len(pre), 1)
        lines = pre[0]["text"].split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("2026-10-04 09:30:00", lines[0])
        self.assertIn("quest.purged", lines[0])
        self.assertIn("@1", lines[0])
        self.assertIn("@system", lines[1])
        self.assertEqual(lines[0].index("quest.purged"), lines[1].index("participant.joined"))
        markup = message.edit_text.await_args.kwargs["reply_markup"]
        self.assertEqual(
            [button.callback_data for row in markup.inline_keyboard for button in row],
            ["super:logs", "admin:home"],
        )


class AggregateMedalTests(unittest.IsolatedAsyncioTestCase):
    async def test_aggregate_rows_use_the_same_medals(self) -> None:
        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def aggregate_leaderboard(self, start_at, end_at, limit=30):
                names = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth"]
                return [
                    {
                        "user_id": 20 + index,
                        "full_name": name,
                        "solved": 10 - index,
                        "completed_quests": 6 - index,
                    }
                    for index, name in enumerate(names)
                ]

            async def get_role(self, user_id: int) -> str:
                return "user"

        message = SimpleNamespace(edit_text=AsyncMock())
        callback = SimpleNamespace(
            data="ratings:period:week",
            from_user=SimpleNamespace(id=20),
            message=message,
            answer=AsyncMock(),
        )

        await aggregate_leaderboard(callback, Db())

        payload = json.dumps(
            message.edit_text.await_args.kwargs["rich_message"].model_dump(mode="json"),
            ensure_ascii=False,
        )
        for medal, rank in zip(("🥇", "🥈", "🥉", "🏅", "🎖"), range(1, 6)):
            self.assertIn(f"{medal} {rank}.", payload)
        # The sixth rank stays a plain number.
        self.assertIn('"text": "6. Sixth', payload)


class SoftDeleteVisibilityTests(unittest.IsolatedAsyncioTestCase):
    """A deleted quest record is visible to superadmins only."""

    class Db:
        def __init__(self, role: str) -> None:
            self.role = role
            self.answer_calls: list[dict] = []

        async def get_language(self, user_id: int) -> str:
            return "en"

        async def get_role(self, user_id: int) -> str:
            return self.role

        async def get_quest(self, quest_id: int, include_deleted: bool = False):
            if not include_deleted:
                return None
            return {
                "id": quest_id,
                "owner_id": 9,
                "title": "Gone quest",
                "status": "completed",
                "deleted": 1,
            }

    def _callback(self):
        return SimpleNamespace(
            data="manage:quest:17",
            from_user=SimpleNamespace(id=9),
            message=SimpleNamespace(
                chat=SimpleNamespace(type="private"), edit_text=AsyncMock()
            ),
            answer=AsyncMock(),
        )

    async def test_a_regular_admin_never_sees_a_deleted_quest(self) -> None:
        callback = self._callback()
        await manage_quest_callback(callback, self.Db("admin"))

        callback.message.edit_text.assert_not_awaited()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_a_superadmin_sees_the_deleted_quest_and_can_restore_it(self) -> None:
        callback = self._callback()
        await manage_quest_callback(callback, self.Db("superadmin"))

        markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
        codes = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("manage:restore:17", codes)
        self.assertIn("manage:purge:17", codes)
        payload = json.dumps(
            callback.message.edit_text.await_args.kwargs["rich_message"].model_dump(
                mode="json"
            ),
            ensure_ascii=False,
        )
        self.assertIn("deleted", payload.lower())


class PurgeQuestHandlerTests(unittest.IsolatedAsyncioTestCase):
    class Db:
        def __init__(self, role: str = "superadmin") -> None:
            self.role = role
            self.purged: list[int] = []

        async def get_language(self, user_id: int) -> str:
            return "en"

        async def get_role(self, user_id: int) -> str:
            return self.role

        async def get_quest(self, quest_id: int, include_deleted: bool = False):
            return {
                "id": quest_id,
                "owner_id": 9,
                "title": "Gone quest",
                "status": "completed",
                "deleted": 1,
            }

        async def quest_archive_references(self, quest_id: int):
            return [(555, 777)]

        async def purge_quest(self, quest_id: int, actor_id: int) -> bool:
            self.purged.append(quest_id)
            return True

    async def test_only_a_superadmin_gets_the_purge_confirmation(self) -> None:
        callback = SimpleNamespace(
            data="manage:purge:17",
            from_user=SimpleNamespace(id=9),
            bot=SimpleNamespace(),
            message=SimpleNamespace(
                chat=SimpleNamespace(type="private"), answer=AsyncMock()
            ),
            answer=AsyncMock(),
        )
        await confirm_quest_purge(callback, self.Db("admin"))

        callback.message.answer.assert_not_awaited()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_confirming_erases_the_quest_and_its_archived_copies(self) -> None:
        db = self.Db()
        bot = SimpleNamespace(delete_message=AsyncMock())
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"), edit_text=AsyncMock()
        )
        callback = SimpleNamespace(
            data="manage:purgeconfirm:17",
            from_user=SimpleNamespace(id=1),
            message=message,
            answer=AsyncMock(),
        )

        await purge_quest_confirmed(callback, db, bot)

        self.assertEqual(db.purged, [17])
        self.assertEqual(
            bot.delete_message.await_args.kwargs,
            {"chat_id": 555, "message_id": 777},
        )
        self.assertEqual(
            message.edit_text.await_args.kwargs["text"],
            TEXTS["quest_purged"]["en"].format(title="Gone quest"),
        )


class LeadershipNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def test_exhausted_attempts_offer_the_quest_page(self) -> None:
        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def submit_answer(self, quest_id, user_id, answer, now):
                return {"code": "wrong", "exhausted": True}

            async def participant(self, quest_id: int, user_id: int):
                return {"ban_reason": None}

            async def maybe_complete_quest(self, quest_id: int) -> bool:
                return True

        message = SimpleNamespace(
            from_user=SimpleNamespace(id=20), text="last guess", answer=AsyncMock()
        )
        await _process_answer(message, 17, Db(), SimpleNamespace())

        self.assertEqual(
            message.answer.await_args.args[0], TEXTS["attempts_exhausted"]["en"]
        )
        markup = message.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "quest:view:my:all:0:17")

    async def test_a_finished_participation_offers_the_quest_page(self) -> None:
        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def submit_answer(self, quest_id, user_id, answer, now):
                return {"code": "correct", "final": True}

            async def maybe_complete_quest(self, quest_id: int) -> bool:
                return True

        message = SimpleNamespace(
            from_user=SimpleNamespace(id=20), text="final answer", answer=AsyncMock()
        )
        await _process_answer(message, 17, Db(), SimpleNamespace())

        self.assertEqual(message.answer.await_args.args[0], TEXTS["correct_done"]["en"])
        markup = message.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "quest:view:my:all:0:17")


class QuestDurationEditTests(unittest.IsolatedAsyncioTestCase):
    """A running quest's overall time can be changed from the admin panel."""

    class Db:
        def __init__(self) -> None:
            self.updated: list[tuple[int, int]] = []

        async def get_language(self, user_id: int) -> str:
            return "en"

        async def get_role(self, user_id: int) -> str:
            return "admin"

        async def get_quest(self, quest_id: int) -> dict:
            return {
                "id": quest_id,
                "owner_id": 5,
                "status": "active",
                "start_at": "2026-10-05T06:00:00+00:00",
                "duration_seconds": 0,
                "title": "Timed",
            }

        async def update_quest_duration(self, quest_id, duration, actor_id, now):
            self.updated.append((quest_id, int(duration)))
            return {"duration_seconds": 0}

    def _message(self, text: str):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=5),
            text=text,
            answer=AsyncMock(),
            answer_rich=AsyncMock(),
        )

    async def test_minutes_update_the_quest_time(self) -> None:
        db = self.Db()
        state = FakeState({"edit_quest_id": 17, "edit_context": "quest_metadata"})
        message = self._message("120")

        await quest_duration_received(message, state, db)

        self.assertEqual(db.updated, [(17, 7200)])
        self.assertIsNone(state.current_state)
        sent = message.answer_rich.await_args.args[0]
        self.assertIn(TEXTS["quest_duration_updated"]["en"], sent.blocks[0].text)

    async def test_an_end_time_updates_the_quest_time(self) -> None:
        db = self.Db()
        state = FakeState({"edit_quest_id": 17, "edit_context": "quest_metadata"})
        message = self._message("2026-10-05 08:00")

        await quest_duration_received(message, state, db)

        # 06:00 UTC start, local Tashkent 08:00 is 03:00 UTC -> too early.
        self.assertEqual(db.updated, [])
        self.assertEqual(
            message.answer.await_args.args[0],
            f"⚠️ {TEXTS['invalid_quest_end']['en']}",
        )

    async def test_a_later_end_time_is_accepted(self) -> None:
        db = self.Db()
        state = FakeState({"edit_quest_id": 17, "edit_context": "quest_metadata"})
        message = self._message("2026-10-05 13:00")

        await quest_duration_received(message, state, db)

        self.assertEqual(db.updated, [(17, 2 * 3600)])

    async def test_zero_removes_the_time_limit(self) -> None:
        db = self.Db()
        state = FakeState({"edit_quest_id": 17, "edit_context": "quest_metadata"})
        message = self._message("0")

        await quest_duration_received(message, state, db)

        self.assertEqual(db.updated, [(17, 0)])


class BackButtonAuditTests(unittest.IsolatedAsyncioTestCase):
    """Every Back button walks one level up instead of skipping a section."""

    def setUp(self) -> None:
        navigation.reset()

    async def test_browse_list_rows_carry_their_filter_and_page(self) -> None:
        markup = browse_filters(
            "en", "active", 2, [{"id": 17, "title": "Night Quest"}], 10
        )
        self.assertEqual(
            markup.inline_keyboard[2][0].callback_data,
            "quest:view:17:browse:active:2",
        )
        card = quest_detail(
            "en",
            {
                "id": 17,
                "title": "Night Quest",
                "visibility": "public",
                "status": "active",
                "progression": "immediate",
            },
            browse_status="active",
            browse_page=2,
        )
        codes = [button.callback_data for row in card.inline_keyboard for button in row]
        self.assertIn("rating:show:17:card:browse:active:2", codes)
        self.assertEqual(codes[-1], "browse:filter:active:2")

    async def test_a_quest_card_callback_keeps_its_browse_origin(self) -> None:
        parsed = _parse_quest_card("quest:view:17:browse:active:2")
        self.assertEqual(parsed, (17, False, "all", 0, "active", 2))
        self.assertEqual(
            _leaderboard_back_target("rating:show:17:card:browse:active:2", 17),
            "quest:view:17:browse:active:2",
        )
        self.assertEqual(
            _leaderboard_back_target("rating:show:17:card:my:private:3", 17),
            "quest:view:my:private:3:17",
        )

    async def test_the_quest_card_back_returns_to_the_browse_page(self) -> None:
        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def get_role(self, user_id: int) -> str:
                return "user"

            async def get_quest(self, quest_id: int) -> dict:
                return {
                    "id": quest_id,
                    "owner_id": 5,
                    "visibility": "public",
                    "status": "active",
                    "progression": "immediate",
                    "title": "Night Quest",
                    "description": "",
                    "start_at": "2030-01-02T00:00:00+00:00",
                    "duration_seconds": 0,
                    "stage_count": 2,
                    "chat_id": None,
                }

            async def participant(self, quest_id: int, user_id: int):
                return None

            async def participant_count(self, quest_id: int) -> int:
                return 0

            async def next_deliverable_stage(self, quest_id: int, user_id: int, now: str):
                return None

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"), edit_text=AsyncMock(), answer=AsyncMock()
        )
        callback = SimpleNamespace(
            data="quest:view:17:browse:active:2",
            from_user=SimpleNamespace(id=1),
            message=message,
            answer=AsyncMock(),
        )

        await view_quest_callback(callback, Db(), AsyncMock())

        markup = message.edit_text.await_args.kwargs["reply_markup"]
        codes = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("browse:filter:active:2", codes)

    async def test_the_manage_card_remembers_the_list_that_opened_it(self) -> None:
        class Db:
            def __init__(self) -> None:
                self.quest = {
                    "id": 17,
                    "owner_id": 5,
                    "visibility": "public",
                    "status": "active",
                    "progression": "immediate",
                    "title": "Night Quest",
                    "description": "",
                    "start_at": "2030-01-02T00:00:00+00:00",
                }

            async def get_language(self, user_id: int) -> str:
                return "en"

            async def get_role(self, user_id: int) -> str:
                return "superadmin"

            async def get_quest(self, quest_id: int, include_deleted: bool = False):
                return self.quest

            async def participant_count(self, quest_id: int) -> int:
                return 1

        db = Db()

        def callback(data: str):
            return SimpleNamespace(
                data=data,
                from_user=SimpleNamespace(id=3),
                message=SimpleNamespace(
                    chat=SimpleNamespace(type="private"), edit_text=AsyncMock()
                ),
                answer=AsyncMock(),
            )

        from_list = callback("manage:quest:17:super:adminquests:5:2")
        await manage_quest_callback(from_list, db)
        codes = [
            button.callback_data
            for row in from_list.message.edit_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertIn("super:adminquests:5:2", codes)

        # A sub-screen re-opens the card without an origin and must keep it.
        from_sub_screen = callback("manage:quest:17")
        await manage_quest_callback(from_sub_screen, db)
        codes = [
            button.callback_data
            for row in from_sub_screen.message.edit_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertIn("super:adminquests:5:2", codes)

    async def test_admin_detail_and_chat_detail_keep_their_list_page(self) -> None:
        admin = {"telegram_id": 5, "role": "admin", "full_name": "Ann"}
        self.assertEqual(
            [
                button.callback_data
                for row in admin_detail_keyboard("en", admin, 3, 2).inline_keyboard
                for button in row
            ][-2],
            "super:admins:2",
        )
        self.assertEqual(
            [
                button.callback_data
                for row in manage_chat_keyboard(
                    "en", {"chat_id": -100, "title": "Chat"}, 3
                ).inline_keyboard
                for button in row
            ][-2],
            "super:chats:3",
        )

    async def test_a_removed_whitelist_entry_stays_on_its_page(self) -> None:
        recorded: list = []

        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def get_role(self, user_id: int) -> str:
                return "superadmin"

            async def settings_get(self, key: str, default: str) -> str:
                return "10"

            async def chat_whitelist_count(self, chat_id: int) -> int:
                return 30

            async def chat_whitelist(self, chat_id: int, page: int, page_size: int):
                recorded.append(page)
                return [{"user_id": 900 + page}]

            async def remove_chat_whitelist(self, chat_id: int, user_id: int) -> None:
                return None

            async def log_action(self, *args, **kwargs) -> None:
                return None

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"), edit_text=AsyncMock()
        )
        callback = SimpleNamespace(
            data="super:delwhite:-100:900:2",
            from_user=SimpleNamespace(id=3),
            message=message,
            answer=AsyncMock(),
        )

        await remove_whitelist(callback, Db())

        self.assertEqual(recorded, [2])

    async def test_ticket_history_and_ticket_list_have_back_buttons(self) -> None:
        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def settings_get(self, key: str, default: str) -> str:
                return "10"

            async def support_quest_count(self, user_id: int) -> int:
                return 0

            async def support_quests_for_user(self, user_id: int, page: int, size: int):
                return []

            async def user_tickets_page(self, user_id: int, page: int, size: int):
                return [{"id": 5, "status": "open"}], 1, page

            async def get_ticket(self, ticket_id: int):
                return {"id": ticket_id, "user_id": 1, "status": "open", "language": "en"}

            async def ticket_messages(self, ticket_id: int):
                return []

            async def get_role(self, user_id: int) -> str:
                return "admin"

        def callback(data: str):
            return SimpleNamespace(
                data=data,
                from_user=SimpleNamespace(id=1),
                message=SimpleNamespace(
                    chat=SimpleNamespace(type="private"), edit_text=AsyncMock()
                ),
                answer=AsyncMock(),
            )

        db = Db()
        await open_support_page(callback("support:open:1"), db)
        tickets = callback("support:tickets:0")
        await list_my_tickets_page(tickets, db)
        codes = [
            button.callback_data
            for row in tickets.message.edit_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertIn("support:ticket:5:0", codes)
        self.assertIn("support:open:1", codes)

        history = callback("support:ticket:5:0")
        await show_ticket_history(history, db)
        codes = [
            button.callback_data
            for row in history.message.edit_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertIn("support:tickets:0", codes)

    async def test_admin_and_chat_details_use_the_remembered_list_page(
        self,
    ) -> None:
        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

            async def get_role(self, user_id: int) -> str:
                return "superadmin"

            async def settings_get(self, key: str, default: str) -> str:
                return "10"

            async def list_admins_page(self, page: int, page_size: int):
                return (
                    [
                        {
                            "telegram_id": 5,
                            "role": "admin",
                            "full_name": "Ann",
                        }
                    ],
                    30,
                )

            async def managed_chats_page(self, page: int, page_size: int):
                return ([{"chat_id": -100, "title": "Chat"}], 30, page)

            async def list_admins(self):
                return [{"telegram_id": 5, "role": "admin", "full_name": "Ann"}]

            async def list_owner_quests(
                self, admin_id: int, include_deleted: bool = False
            ):
                return []

            async def get_managed_chat(self, chat_id: int):
                return {
                    "chat_id": chat_id,
                    "title": "Chat",
                    "cleanup_enabled": False,
                }

        def callback(data: str):
            return SimpleNamespace(
                data=data,
                from_user=SimpleNamespace(id=3),
                message=SimpleNamespace(
                    chat=SimpleNamespace(type="private"), edit_text=AsyncMock()
                ),
                answer=AsyncMock(),
            )

        db = Db()
        await show_admins_page(callback("super:admins:2"), db)
        detail = callback("super:admin:5")
        await show_admin_detail(detail, db)
        codes = [
            button.callback_data
            for row in detail.message.edit_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertIn("super:admins:2", codes)

        await show_managed_chats_page(callback("super:chats:3"), db)
        chat = callback("super:chat:-100")
        await show_chat(chat, db)
        codes = [
            button.callback_data
            for row in chat.message.edit_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertIn("super:chats:3", codes)

    async def test_deleted_and_review_screens_offer_the_way_back(self) -> None:
        codes = [
            button.callback_data
            for row in deleted_quest_keyboard(
                "en", 17, "super:adminquests:5:2"
            ).inline_keyboard
            for button in row
        ]
        self.assertIn("super:adminquests:5:2", codes)

        codes = [
            button.callback_data
            for row in review_keyboard("en", 4, "manage:quest:17").inline_keyboard
            for button in row
        ]
        self.assertIn("manage:quest:17", codes)

        codes = [
            button.callback_data
            for row in ticket_reply_keyboard(
                "en", 5, "support:tickets:1"
            ).inline_keyboard
            for button in row
        ]
        self.assertIn("support:tickets:1", codes)


class GuideKeyboardTests(unittest.TestCase):
    def test_the_guide_ends_with_a_main_menu_button(self) -> None:
        for role in (None, "user", "admin", "superadmin"):
            with self.subTest(role=role):
                codes = [
                    button.callback_data
                    for row in guide_keyboard("en", role).inline_keyboard
                    for button in row
                ]
                self.assertEqual(codes[-1], "menu:home")
                self.assertIn("menu:guide", codes)
                if role in {"admin", "superadmin"}:
                    self.assertIn("admin:home", codes)


class QuestDurationTests(unittest.IsolatedAsyncioTestCase):
    """Creation accepts minutes or an explicit end time."""

    class Db:
        async def get_language(self, user_id: int) -> str:
            return "en"

        async def managed_chats(self):
            return []

    async def test_minutes_are_stored_as_seconds(self) -> None:
        state = FakeState({"start_at": "2026-10-05T06:00:00+00:00"})
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=5), text="90", answer=AsyncMock()
        )

        await duration_received(message, state, self.Db())

        self.assertEqual(state.data["duration_seconds"], 5400)
        self.assertEqual(state.current_state, CreateQuest.chat)

    async def test_an_end_time_sets_the_duration_of_the_quest(self) -> None:
        state = FakeState({"start_at": "2026-10-05T06:00:00+00:00"})
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=5),
            text="2026-10-05 13:00",
            answer=AsyncMock(),
        )

        await duration_received(message, state, self.Db())

        # Local Tashkent 13:00 is 08:00 UTC, two hours after the start.
        self.assertEqual(state.data["duration_seconds"], 2 * 3600)
        self.assertEqual(state.current_state, CreateQuest.chat)

    async def test_an_end_time_before_the_start_is_rejected(self) -> None:
        state = FakeState({"start_at": "2026-10-05T06:00:00+00:00"})
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=5),
            text="2026-10-05 05:00",
            answer=AsyncMock(),
        )

        await duration_received(message, state, self.Db())

        self.assertEqual(
            message.answer.await_args.args[0], TEXTS["invalid_quest_end"]["en"]
        )
        self.assertNotIn("duration_seconds", state.data)
        self.assertIsNone(state.current_state)

    async def test_zero_means_no_time_limit(self) -> None:
        state = FakeState({"start_at": "2026-10-05T06:00:00+00:00"})
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=5), text="0", answer=AsyncMock()
        )

        await duration_received(message, state, self.Db())

        self.assertEqual(state.data["duration_seconds"], 0)
        self.assertEqual(state.current_state, CreateQuest.chat)


class StartLeadTimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_creation_requires_a_ten_minute_lead_time(self) -> None:
        class Db:
            async def get_language(self, user_id: int) -> str:
                return "en"

        def stamp(minutes: int) -> str:
            moment = datetime.now(ZoneInfo("Asia/Tashkent")) + timedelta(minutes=minutes)
            return moment.strftime("%Y-%m-%d %H:%M")

        too_soon = SimpleNamespace(
            from_user=SimpleNamespace(id=5), text=stamp(5), answer=AsyncMock()
        )
        state = FakeState()
        await start_time_received(too_soon, state, Db())

        self.assertEqual(
            too_soon.answer.await_args.args[0],
            TEXTS["invalid_quest_start_min"]["en"],
        )
        self.assertNotIn("start_at", state.data)
        self.assertIsNone(state.current_state)

        accepted = SimpleNamespace(
            from_user=SimpleNamespace(id=5), text=stamp(15), answer=AsyncMock()
        )
        state = FakeState()
        await start_time_received(accepted, state, Db())

        self.assertIn("start_at", state.data)
        self.assertEqual(state.current_state, CreateQuest.duration)


class ListPaginationTests(unittest.TestCase):
    def test_admin_list_paginates_with_the_configured_page_size(self) -> None:
        admins = [
            {"telegram_id": user_id, "role": "admin", "full_name": f"Admin {user_id}"}
            for user_id in range(1, 11)
        ]
        first = admin_list_keyboard("en", admins, 0, 10).inline_keyboard
        # Ten administrator rows, one navigation row, Add, and the home button.
        self.assertEqual(len(first), 13)
        self.assertEqual(first[-1][0].callback_data, "admin:home")

        codes = [button.callback_data for row in first for button in row]
        self.assertIn("super:admins:1", codes)
        self.assertNotIn("super:admins:-1", codes)

        last = admin_list_keyboard("en", admins[:3], 1, 10).inline_keyboard
        codes = [button.callback_data for row in last for button in row]
        self.assertIn("super:admins:0", codes)
        self.assertNotIn("super:admins:2", codes)

    def test_chat_list_paginates(self) -> None:
        chats = [{"chat_id": index, "title": f"Chat {index}"} for index in range(1, 11)]
        codes = [
            button.callback_data
            for row in managed_chats_keyboard("en", chats, 0, 10).inline_keyboard
            for button in row
        ]
        self.assertIn("super:chats:1", codes)

        codes = [
            button.callback_data
            for row in managed_chats_keyboard("en", chats[:2], 1, 10).inline_keyboard
            for button in row
        ]
        self.assertIn("super:chats:0", codes)
        self.assertNotIn("super:chats:2", codes)


class AnswerButtonTests(unittest.TestCase):
    def test_main_menu_button_sits_under_the_answer_button(self) -> None:
        markup = answer_button("en", "https://t.me/quest_bot?start=play_7", "quest_bot")
        self.assertEqual(len(markup.inline_keyboard), 2)
        self.assertEqual(markup.inline_keyboard[0][0].text, TEXTS["btn_answer_privately"]["en"])
        self.assertEqual(markup.inline_keyboard[0][0].url, "https://t.me/quest_bot?start=play_7")
        self.assertEqual(markup.inline_keyboard[1][0].text, TEXTS["btn_home"]["en"])
        self.assertEqual(markup.inline_keyboard[1][0].url, "https://t.me/quest_bot")


class UserFacingRatingsTests(unittest.IsolatedAsyncioTestCase):
    """User-facing ratings never show the Administrator panel button."""

    class Db:
        def __init__(self, role: str = "superadmin", owner_id: int = 9) -> None:
            self.role = role
            self.owner_id = owner_id

        async def get_language(self, user_id: int) -> str:
            return "en"

        async def get_role(self, user_id: int) -> str:
            return self.role

        async def get_quest(self, quest_id: int) -> dict:
            return {
                "id": quest_id,
                "owner_id": self.owner_id,
                "visibility": "public",
                "title": "Night Quest",
            }

        async def participant(self, quest_id: int, user_id: int):
            return None

        async def leaderboard(self, quest_id: int):
            return []

        async def aggregate_leaderboard(self, start_at, end_at, limit=30):
            return []

        async def list_public_quests(self, status, offset, limit):
            return []

        async def list_manageable_quests(
            self, requester_id, is_superadmin, status, offset, limit, deleted=False
        ):
            return []

        async def settings_get(self, key: str, default: str) -> str:
            return "10"

    def _callback(self, data: str, chat_type: str = "private"):
        message = SimpleNamespace(
            chat=SimpleNamespace(type=chat_type), edit_text=AsyncMock()
        )
        return SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=9),
            message=message,
            answer=AsyncMock(),
        )

    async def test_overview_of_a_superadmin_offers_the_main_menu_only(self) -> None:
        callback = self._callback("ratings:overview")
        await ratings_overview(callback, self.Db())

        codes = [
            button.callback_data
            for row in callback.message.edit_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertIn("menu:home", codes)
        self.assertNotIn("admin:home", codes)
        self.assertNotIn("ratings:list:managed:0", codes)

    async def test_aggregate_leaderboard_returns_to_the_main_menu(self) -> None:
        callback = self._callback("ratings:period:week")
        await aggregate_leaderboard(callback, self.Db())

        codes = [
            button.callback_data
            for row in callback.message.edit_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertEqual(codes, ["ratings:overview", "menu:home"])

    async def test_quest_rating_opened_from_a_card_returns_to_the_main_menu(self) -> None:
        callback = self._callback("rating:show:17:card:browse")
        await show_leaderboard(callback, self.Db())

        codes = [
            button.callback_data
            for row in callback.message.edit_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertEqual(codes, ["quest:view:17", "menu:home"])

    async def test_quest_rating_opened_from_the_admin_side_keeps_the_panel_button(
        self,
    ) -> None:
        callback = self._callback("rating:show:17:manage")
        await show_leaderboard(callback, self.Db())

        codes = [
            button.callback_data
            for row in callback.message.edit_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertEqual(codes, ["manage:quest:17", "admin:home"])

    async def test_public_ratings_list_has_no_admin_panel_button(self) -> None:
        callback = self._callback("ratings:list:all:0")
        await leaderboard_quest_list(callback, self.Db())

        codes = [
            button.callback_data
            for row in callback.message.edit_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertIn("menu:home", codes)
        self.assertNotIn("admin:home", codes)

        managed = self._callback("ratings:list:managed:0")
        await leaderboard_quest_list(managed, self.Db())
        codes = [
            button.callback_data
            for row in managed.message.edit_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertIn("admin:home", codes)
        self.assertNotIn("menu:home", codes)
        # Back returns to the admin panel, not to the public ratings screen.
        self.assertEqual(codes[0], "admin:home")
        self.assertNotIn("ratings:overview", codes)


class PaginatedListHandlerTests(unittest.IsolatedAsyncioTestCase):
    class Db:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        async def get_language(self, user_id: int) -> str:
            return "en"

        async def get_role(self, user_id: int) -> str:
            return "superadmin"

        async def settings_get(self, key: str, default: str) -> str:
            return "10"

        async def list_admins_page(self, page: int, page_size: int):
            self.calls.append(("admins", page, page_size))
            return (
                [
                    {
                        "telegram_id": user_id,
                        "role": "admin",
                        "full_name": f"Admin {user_id}",
                    }
                    for user_id in range(1, 11)
                ],
                12,
            )

        async def managed_chats_page(self, page: int, page_size: int):
            self.calls.append(("chats", page, page_size))
            return (
                [
                    {"chat_id": -100 - index, "title": f"Group {index}"}
                    for index in range(1, 11)
                ],
                12,
                page,
            )

        async def user_tickets_page(self, user_id: int, page: int, page_size: int):
            self.calls.append(("tickets", user_id, page, page_size))
            return (
                [{"id": 5 + index, "status": "open"} for index in range(10)],
                12,
                page,
            )

        async def support_quest_count(self, user_id: int) -> int:
            return 12

        async def support_quests_for_user(self, user_id: int, page: int = 0, page_size: int = 1000):
            self.calls.append(("support", user_id, page, page_size))
            return [
                {"id": 7 + index, "title": f"Quest {index}", "owner_id": 5}
                for index in range(10)
            ]

    def _callback(self, data: str):
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"), edit_text=AsyncMock()
        )
        return SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=1),
            message=message,
            answer=AsyncMock(),
        )

    async def test_admin_list_uses_the_configured_page_size(self) -> None:
        db = self.Db()
        callback = self._callback("super:admins:1")
        await show_admins_page(callback, db)

        self.assertEqual(db.calls, [("admins", 1, 10)])
        markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
        codes = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("super:admins:0", codes)
        self.assertIn("super:admins:2", codes)
        self.assertEqual(codes[-1], "admin:home")

    async def test_chat_list_pages_through_registered_chats(self) -> None:
        db = self.Db()
        callback = self._callback("super:chats:1")
        await show_managed_chats_page(callback, db)

        self.assertEqual(db.calls, [("chats", 1, 10)])
        markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
        codes = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("super:chats:0", codes)
        self.assertIn("super:chats:2", codes)

    async def test_ticket_list_pages_through_a_users_tickets(self) -> None:
        db = self.Db()
        # The user reached the tickets from the support screen; Back must
        # return to that screen rather than skipping to the main menu.
        support = self._callback("support:open:0")
        await open_support_page(support, db)
        callback = self._callback("support:tickets:1")
        await list_my_tickets_page(callback, db)

        self.assertEqual(db.calls[-1], ("tickets", 1, 1, 10))
        markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
        codes = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("support:tickets:0", codes)
        self.assertIn("support:tickets:2", codes)
        self.assertIn("support:open:0", codes)

    async def test_support_screen_pages_through_the_users_quests(self) -> None:
        db = self.Db()
        callback = self._callback("support:open:1")
        await open_support_page(callback, db)

        self.assertEqual(db.calls, [("support", 1, 1, 10)])
        markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
        codes = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("support:open:0", codes)
        self.assertIn("support:open:2", codes)


if __name__ == "__main__":
    unittest.main()
