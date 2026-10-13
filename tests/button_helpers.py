"""Helpers for asserting controls regardless of their Bot API transport."""

from __future__ import annotations

from types import SimpleNamespace

from aiogram.types import InputRichMessage


def keyboard_from_rich(content: InputRichMessage) -> SimpleNamespace | None:
    """Expose rich-message button rows in keyboard-like form for assertions."""
    rows = [
        block.buttons
        for block in (content.blocks or [])
        if getattr(block, "type", None) == "buttons"
    ]
    return SimpleNamespace(inline_keyboard=rows) if rows else None


def keyboard_from_call(call) -> InlineKeyboardMarkup | None:
    """Read a keyboard from a mocked aiogram call, rich or traditional."""
    markup = call.kwargs.get("reply_markup")
    if markup is not None:
        return markup
    content = call.kwargs.get("rich_message")
    if not isinstance(content, InputRichMessage):
        content = next(
            (
                argument
                for argument in call.args
                if isinstance(argument, InputRichMessage)
            ),
            None,
        )
    return keyboard_from_rich(content) if content is not None else None
