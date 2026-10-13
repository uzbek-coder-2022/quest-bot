"""Regression coverage for localized, consistent user-feedback presentation."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from quest_bot.feedback import feedback_level, feedback_message, feedback_text
from quest_bot.localization import LANGUAGES, TEXTS
from quest_bot.utils import answer_feedback, ensure_private_callback


class FeedbackPresentationTests(unittest.TestCase):
    def test_feedback_cards_use_localized_headings_and_one_severity_marker(self) -> None:
        cases = (
            ("error_generic", "error"),
            ("invalid_text", "warning"),
            ("no_pending", "info"),
            ("join_success", "success"),
            ("edit_quest_cover_missing", "warning"),
            ("whitelist_added", "success"),
            ("whitelist_removed", "success"),
        )
        for language in LANGUAGES:
            for key, level in cases:
                with self.subTest(language=language, key=key):
                    payload = feedback_message(language, key).model_dump(
                        mode="json", exclude_none=True
                    )
                    self.assertEqual(
                        payload["blocks"][0]["text"],
                        TEXTS[f"feedback_{level}_title"][language],
                    )
                    self.assertEqual(payload["blocks"][1]["type"], "paragraph")
                    self.assertTrue(payload["blocks"][1]["text"])
                    self.assertEqual(feedback_level(key), level)

    def test_legacy_status_icons_are_replaced_not_duplicated(self) -> None:
        wrong_answer = feedback_message("en", "wrong_answer", remaining=2)
        warning_body = wrong_answer.blocks[1].text
        self.assertFalse(str(warning_body).startswith("❌"))
        self.assertIn("Attempts remaining: 2", str(warning_body))
        self.assertEqual(
            feedback_text("en", "wrong_answer", remaining=2).count("❌"), 0
        )
        self.assertTrue(feedback_text("en", "wrong_answer", remaining=2).startswith("⚠️ "))

    def test_feedback_content_remains_literal(self) -> None:
        card = feedback_message("en", "global_blocked", reason=" <b>literal</b>")
        body = card.blocks[1].text
        self.assertIn("<b>literal</b>", str(body))


class FeedbackDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_answer_feedback_sends_a_rich_card_and_keeps_buttons(self) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Skip photo", callback_data="skip")]
            ]
        )
        message = SimpleNamespace(answer=AsyncMock(), answer_rich=AsyncMock())

        await answer_feedback(
            message, "en", "invalid_cover_photo", reply_markup=markup
        )

        message.answer.assert_not_awaited()
        message.answer_rich.assert_awaited_once()
        call = message.answer_rich.await_args
        self.assertIsNone(call.kwargs["reply_markup"])
        blocks = call.args[0].blocks
        self.assertEqual(blocks[0].text, TEXTS["feedback_warning_title"]["en"])
        self.assertEqual(blocks[-1].type, "buttons")
        self.assertEqual(blocks[-1].buttons[0].callback_data, "skip")

    async def test_answer_feedback_fallback_preserves_buttons(self) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Open quest", callback_data="open")]
            ]
        )
        message = SimpleNamespace(answer=AsyncMock())

        await answer_feedback(message, "en", "join_success", reply_markup=markup)

        message.answer.assert_awaited_once_with(
            feedback_text("en", "join_success"), reply_markup=markup
        )

    async def test_answer_feedback_falls_back_to_compact_text_for_legacy_clients(self) -> None:
        message = SimpleNamespace(answer=AsyncMock())

        await answer_feedback(message, "en", "invalid_text")

        message.answer.assert_awaited_once_with(
            "⚠️ Text must be non-empty and no longer than 1,000 characters. Try again.",
            reply_markup=None,
        )

    async def test_private_callback_error_uses_compact_feedback(self) -> None:
        callback = SimpleNamespace(
            message=SimpleNamespace(chat=SimpleNamespace(type="group")),
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )
        db = SimpleNamespace(get_language=AsyncMock(return_value="en"))

        allowed = await ensure_private_callback(callback, db)

        self.assertFalse(allowed)
        callback.answer.assert_awaited_once_with(
            feedback_text("en", "open_private_chat"), show_alert=True
        )


class FeedbackCallSiteAuditTests(unittest.TestCase):
    def test_direct_status_messages_use_the_shared_presenter(self) -> None:
        source_root = Path(__file__).parents[1] / "quest_bot" / "handlers"
        for path in source_root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                method = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                if method == "answer" and node.args:
                    content = node.args[0]
                elif method in {"answer_with_buttons", "answer_rich_with_buttons"} and len(
                    node.args
                ) > 1:
                    content = node.args[1]
                else:
                    continue
                if not isinstance(content, ast.JoinedStr) and not (
                    isinstance(content, ast.Call)
                    and isinstance(content.func, ast.Name)
                    and content.func.id == "tr"
                ):
                    continue
                translations = [
                    item
                    for item in ast.walk(content)
                    if isinstance(item, ast.Call)
                    and isinstance(item.func, ast.Name)
                    and item.func.id == "tr"
                    and len(item.args) > 1
                    and isinstance(item.args[1], ast.Constant)
                    and isinstance(item.args[1].value, str)
                ]
                for translation in translations:
                    key = translation.args[1].value
                    try:
                        feedback_level(key)
                    except KeyError:
                        continue
                    self.fail(
                        f"Unformatted feedback key {key!r} in "
                        f"{path.relative_to(source_root.parent)}:{node.lineno}"
                    )

    def test_dynamic_callback_alerts_use_the_shared_presenter(self) -> None:
        source_root = Path(__file__).parents[1] / "quest_bot" / "handlers"
        for path in source_root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "answer"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "callback"
                    and node.args
                ):
                    continue
                content = node.args[0]
                if not (
                    isinstance(content, ast.Call)
                    and isinstance(content.func, ast.Name)
                    and content.func.id == "tr"
                    and len(content.args) > 1
                    and isinstance(content.args[1], ast.Name)
                ):
                    continue
                is_alert = any(
                    keyword.arg == "show_alert"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                    for keyword in node.keywords
                )
                if is_alert:
                    self.fail(
                        "Dynamic callback alerts must use feedback_text, not tr(), "
                        f"in {path.relative_to(source_root.parent)}:{node.lineno}"
                    )


if __name__ == "__main__":
    unittest.main()
