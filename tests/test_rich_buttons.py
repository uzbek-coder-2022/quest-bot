from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import (
    CallbackGame,
    CopyTextButton,
    DisabledButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichBlockParagraph,
    InputRichMessage,
    LoginUrl,
    WebAppInfo,
)

from quest_bot.keyboards import copy_link_keyboard, language_keyboard
from quest_bot.rich_buttons import (
    embed_inline_keyboard,
    inline_keyboard_to_rich_blocks,
    rich_button,
    rich_button_row,
)
from quest_bot.utils import answer_with_buttons, send_with_buttons


class RichMessageButtonTests(unittest.TestCase):
    def test_keyboard_rows_embed_with_styles_alignment_and_copy_actions(self) -> None:
        content = InputRichMessage(
            blocks=[InputRichBlockParagraph(text="Quest details")]
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Join", callback_data="quest:join:7", style="success"
                    ),
                    InlineKeyboardButton(
                        text="Cancel", callback_data="quest:cancel:7", style="danger"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Copy", copy_text=CopyTextButton(text="invite-link")
                    )
                ],
            ]
        )

        enriched, keyboard = embed_inline_keyboard(content, markup, align="right")

        self.assertIsNone(keyboard)
        self.assertEqual(enriched.blocks[0].text, "Quest details")
        rows = [block for block in enriched.blocks if block.type == "buttons"]
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.align for row in rows], ["right", "right"])
        self.assertEqual(
            [button.style for button in rows[0].buttons], ["success", "danger"]
        )
        self.assertEqual(
            [button.callback_data for button in rows[0].buttons],
            ["quest:join:7", "quest:cancel:7"],
        )
        self.assertEqual(rows[1].buttons[0].copy_text.text, "invite-link")

    def test_plain_text_becomes_a_rich_paragraph_when_buttons_are_supported(self) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Choose", callback_data="choose")]]
        )

        enriched, remaining = embed_inline_keyboard("Choose an option", markup)

        self.assertIsNone(remaining)
        self.assertIsInstance(enriched, InputRichMessage)
        self.assertEqual(enriched.blocks[0].type, "paragraph")
        self.assertEqual(enriched.blocks[0].text, "Choose an option")
        self.assertEqual(enriched.blocks[1].type, "buttons")
        self.assertEqual(enriched.blocks[1].buttons[0].callback_data, "choose")

        unsupported = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Pay", pay=True)]]
        )
        plain, remaining = embed_inline_keyboard("Checkout", unsupported)
        self.assertEqual(plain, "Checkout")
        self.assertIs(remaining, unsupported)

    def test_unrepresentable_keyboard_and_non_block_content_keep_the_old_keyboard(
        self,
    ) -> None:
        content = InputRichMessage(blocks=[InputRichBlockParagraph(text="Body")])
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Pay", pay=True)]]
        )

        enriched, retained = embed_inline_keyboard(content, keyboard)

        self.assertIs(enriched, content)
        self.assertIs(retained, keyboard)

        html_content = InputRichMessage(html="<b>Body</b>")
        callback_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Open", callback_data="open")]]
        )
        unchanged, retained = embed_inline_keyboard(html_content, callback_keyboard)
        self.assertIs(unchanged, html_content)
        self.assertIs(retained, callback_keyboard)

    def test_each_rich_button_action_and_link_style_are_validated(self) -> None:
        callback = rich_button("Open", callback_data="open", style="link")
        self.assertEqual(callback.style, "link")

        web_app = rich_button(
            "Mini App", web_app=WebAppInfo(url="https://example.com/app")
        )
        self.assertEqual(web_app.web_app.url, "https://example.com/app")

        login = rich_button(
            "Sign in", login_url=LoginUrl(url="https://example.com/login")
        )
        self.assertEqual(login.login_url.url, "https://example.com/login")

        copy = rich_button("Copy", copy_text="a value")
        self.assertEqual(copy.copy_text.text, "a value")

        disabled = rich_button("Unavailable", disabled=True, style="danger")
        self.assertIsInstance(disabled.disabled, DisabledButton)
        self.assertIsNone(disabled.callback_data)

        with self.assertRaises(ValueError):
            rich_button("Invalid", callback_data="one", url="https://example.com")
        with self.assertRaises(ValueError):
            rich_button("Invalid link", url="https://example.com", style="link")

    def test_mini_app_and_login_actions_survive_inline_keyboard_conversion(
        self,
    ) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Open app",
                        web_app=WebAppInfo(url="https://example.com/app"),
                    ),
                    InlineKeyboardButton(
                        text="Login",
                        login_url=LoginUrl(url="https://example.com/login"),
                    ),
                ]
            ]
        )

        blocks = inline_keyboard_to_rich_blocks(markup, align="left")

        self.assertIsNotNone(blocks)
        self.assertEqual(blocks[0].align, "left")
        self.assertEqual(
            blocks[0].buttons[0].web_app.url, "https://example.com/app"
        )
        self.assertEqual(
            blocks[0].buttons[1].login_url.url, "https://example.com/login"
        )

    def test_language_selection_is_disabled_and_invite_links_have_copy_buttons(
        self,
    ) -> None:
        language = language_keyboard("en")
        selected = language.inline_keyboard[4][0]
        self.assertTrue(selected.text.startswith("✓ "))
        self.assertIsInstance(selected.disabled, DisabledButton)
        self.assertIsNone(selected.callback_data)
        self.assertEqual(language.inline_keyboard[0][0].callback_data, "lang:uz")

        copy = copy_link_keyboard("en", "https://t.me/+invite")
        self.assertEqual(
            copy.inline_keyboard[0][0].copy_text.text, "https://t.me/+invite"
        )

    def test_alignment_and_eight_button_limit_are_enforced(self) -> None:
        row = rich_button_row(
            [
                rich_button("Open", callback_data="open"),
                rich_button("Copy", copy_text="invite"),
            ],
            align="left",
        )
        self.assertEqual(row.align, "left")
        self.assertEqual(len(row.buttons), 2)
        with self.assertRaises(ValueError):
            rich_button_row([])
        with self.assertRaises(ValueError):
            rich_button_row(
                [rich_button("Open", callback_data="open") for _ in range(9)]
            )

        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="x", callback_data="x")]]
        )
        with self.assertRaises(ValueError):
            inline_keyboard_to_rich_blocks(markup, align="middle")

        too_many = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text=str(index), callback_data=f"{index}")
                    for index in range(9)
                ]
            ]
        )
        self.assertIsNone(inline_keyboard_to_rich_blocks(too_many))

        game = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Game", callback_game=CallbackGame())]
            ]
        )
        self.assertIsNone(inline_keyboard_to_rich_blocks(game))


class RichButtonDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_text_send_helpers_embed_buttons_in_rich_messages(self) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Continue", callback_data="continue")]
            ]
        )
        message = SimpleNamespace(answer=AsyncMock(), answer_rich=AsyncMock())
        bot = SimpleNamespace(
            send_message=AsyncMock(), send_rich_message=AsyncMock()
        )

        await answer_with_buttons(message, "Ready?", markup)
        await send_with_buttons(bot, 42, "Ready?", markup)

        message.answer.assert_not_awaited()
        message.answer_rich.assert_awaited_once()
        answer_call = message.answer_rich.await_args
        self.assertIsNone(answer_call.kwargs["reply_markup"])
        self.assertEqual(answer_call.args[0].blocks[0].text, "Ready?")
        self.assertEqual(
            answer_call.args[0].blocks[1].buttons[0].callback_data, "continue"
        )

        bot.send_message.assert_not_awaited()
        bot.send_rich_message.assert_awaited_once()
        sent_call = bot.send_rich_message.await_args
        self.assertEqual(sent_call.args[0], 42)
        self.assertIsNone(sent_call.kwargs["reply_markup"])
        self.assertEqual(sent_call.args[1].blocks[0].text, "Ready?")
        self.assertEqual(
            sent_call.args[1].blocks[1].buttons[0].callback_data, "continue"
        )

    async def test_unsupported_button_actions_keep_the_legacy_keyboard_intact(self) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Pay", pay=True)]]
        )
        message = SimpleNamespace(answer=AsyncMock(), answer_rich=AsyncMock())
        bot = SimpleNamespace(
            send_message=AsyncMock(), send_rich_message=AsyncMock()
        )

        await answer_with_buttons(message, "Checkout", markup)
        await send_with_buttons(bot, 42, "Checkout", markup)

        message.answer_rich.assert_not_awaited()
        message.answer.assert_awaited_once_with("Checkout", reply_markup=markup)
        bot.send_rich_message.assert_not_awaited()
        bot.send_message.assert_awaited_once_with(
            42, "Checkout", reply_markup=markup
        )

    async def test_source_has_no_keyboard_bearing_legacy_message_calls(self) -> None:
        source_root = Path(__file__).parents[1] / "quest_bot"
        for path in source_root.rglob("*.py"):
            if path.name == "utils.py":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(
                    node.func, ast.Attribute
                ):
                    continue
                method = node.func.attr
                if method not in {"answer", "copy_message"} and not method.startswith(
                    ("send_", "edit_")
                ):
                    continue
                if any(keyword.arg == "reply_markup" for keyword in node.keywords):
                    self.fail(
                        f"Legacy keyboard send/edit left at "
                        f"{path.relative_to(source_root.parent)}:{node.lineno}"
                    )


if __name__ == "__main__":
    unittest.main()
