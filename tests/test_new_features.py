from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from quest_bot.database import Database, utc_now
from quest_bot.handlers.creation import (
    answer_mode_selected,
    attempts_mode_different,
    attempts_mode_same,
    common_attempts_received,
)
from quest_bot.handlers.quests import show_leaderboard
from quest_bot.keyboards import (
    admin_panel,
    language_keyboard,
    main_menu,
    quest_detail,
)
from quest_bot.localization import LANGUAGES, TEXTS, yangi_uzbek
from quest_bot.states import CreateQuest


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
        self.assertEqual(start_card.inline_keyboard[0][0].callback_data, "rating:show:17:browse")
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
            card.inline_keyboard[0][0].callback_data, "rating:show:17:my:private:2"
        )


class DatabaseFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
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
        quest = {
            "title": "Scheduled quest",
            "description": "Description",
            "visibility": "public",
            "progression": progression,
            "start_at": self.start_at,
            "duration_seconds": 0,
            "chat_id": None,
            "invite_token": "feature-token",
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
                return []

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
            return message.edit_text.await_args.kwargs["reply_markup"]

        joined = await render("my:private:1", manager=False)
        self.assertEqual(joined.inline_keyboard[0][0].callback_data, "quest:mylist:private:1")
        self.assertEqual(joined.inline_keyboard[1][0].callback_data, "menu:home")

        managed = await render("manage", manager=True)
        self.assertEqual(managed.inline_keyboard[0][0].callback_data, "manage:quest:17")
        self.assertEqual(managed.inline_keyboard[1][0].callback_data, "admin:home")


if __name__ == "__main__":
    unittest.main()
