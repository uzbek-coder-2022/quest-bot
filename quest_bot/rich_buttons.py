"""Bot API 10.3 rich-message button helpers and safe keyboard conversion."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from aiogram.types import (
    CopyTextButton,
    DisabledButton,
    InputRichBlockButtons,
    InputRichBlockParagraph,
    InputRichMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LoginUrl,
    RichMessageButton,
    SwitchInlineQueryChosenChat,
    WebAppInfo,
)

ButtonAlignment = Literal["left", "center", "right"]
RichButtonStyle = Literal["primary", "success", "danger", "link"]

_RICH_BUTTON_STYLES = {"primary", "success", "danger", "link"}
_INLINE_BUTTON_STYLES = {"primary", "success", "danger"}
_RICH_ACTION_FIELDS = (
    "url",
    "callback_data",
    "web_app",
    "login_url",
    "switch_inline_query",
    "switch_inline_query_current_chat",
    "switch_inline_query_chosen_chat",
    "copy_text",
    "disabled",
)


def rich_button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    web_app: WebAppInfo | None = None,
    login_url: LoginUrl | None = None,
    switch_inline_query: str | None = None,
    switch_inline_query_current_chat: str | None = None,
    switch_inline_query_chosen_chat: SwitchInlineQueryChosenChat | None = None,
    copy_text: CopyTextButton | str | None = None,
    disabled: bool = False,
    style: RichButtonStyle | None = None,
) -> RichMessageButton:
    """Build a Bot API 10.3 rich-message button with exactly one action.

    Rich-message buttons can invoke callbacks, open URLs or Mini Apps, request
    Telegram Login, switch inline queries, copy text, or be disabled. The
    ``link`` style is available only for callback buttons, as required by the
    Bot API.
    """
    if not text:
        raise ValueError("Rich button text must not be empty")
    if style is not None and style not in _RICH_BUTTON_STYLES:
        raise ValueError("Rich button style must be primary, success, danger, or link")
    if isinstance(copy_text, str):
        if not 1 <= len(copy_text) <= 256:
            raise ValueError("Copied text must contain between 1 and 256 characters")
        copy_text = CopyTextButton(text=copy_text)
    elif copy_text is not None and not 1 <= len(copy_text.text) <= 256:
        raise ValueError("Copied text must contain between 1 and 256 characters")

    actions: dict[str, object] = {
        "url": url,
        "callback_data": callback_data,
        "web_app": web_app,
        "login_url": login_url,
        "switch_inline_query": switch_inline_query,
        "switch_inline_query_current_chat": switch_inline_query_current_chat,
        "switch_inline_query_chosen_chat": switch_inline_query_chosen_chat,
        "copy_text": copy_text,
        "disabled": DisabledButton() if disabled else None,
    }
    active_actions = [key for key, value in actions.items() if value is not None]
    if len(active_actions) != 1:
        raise ValueError("A rich button must have exactly one action")
    if style == "link" and callback_data is None:
        raise ValueError("The link style is available only for callback buttons")

    return RichMessageButton(text=text, style=style, **actions)


def rich_button_row(
    buttons: Sequence[RichMessageButton],
    *,
    align: ButtonAlignment = "center",
) -> InputRichBlockButtons:
    """Build an aligned rich-message row containing one to eight buttons."""
    if align not in {"left", "center", "right"}:
        raise ValueError("Button alignment must be left, center, or right")
    if not 1 <= len(buttons) <= 8:
        raise ValueError(
            "A rich-message button row must contain between 1 and 8 buttons"
        )
    return InputRichBlockButtons(buttons=list(buttons), align=align)


def copy_button(
    text: str, text_to_copy: str, *, style: str = "primary"
) -> InlineKeyboardButton:
    """Build a standard inline-keyboard button that copies text to clipboard."""
    if not text:
        raise ValueError("Copy button label must not be empty")
    if not 1 <= len(text_to_copy) <= 256:
        raise ValueError("Copied text must contain between 1 and 256 characters")
    if style not in _INLINE_BUTTON_STYLES:
        raise ValueError("Inline button style must be primary, success, or danger")
    return InlineKeyboardButton(
        text=text, copy_text=CopyTextButton(text=text_to_copy), style=style
    )


def _to_rich_button(button: InlineKeyboardButton) -> RichMessageButton | None:
    """Convert a supported inline button; return ``None`` for unsupported types."""
    # Rich-message buttons intentionally do not include payment, game, or
    # custom-emoji buttons. Keep the original keyboard intact for those rows.
    if (
        button.icon_custom_emoji_id is not None
        or button.callback_game is not None
        or button.pay is not None
    ):
        return None
    if button.style is not None and button.style not in _INLINE_BUTTON_STYLES:
        return None
    if sum(getattr(button, field) is not None for field in _RICH_ACTION_FIELDS) != 1:
        return None

    return RichMessageButton(
        text=button.text,
        style=button.style,
        url=button.url,
        callback_data=button.callback_data,
        web_app=button.web_app,
        login_url=button.login_url,
        switch_inline_query=button.switch_inline_query,
        switch_inline_query_current_chat=button.switch_inline_query_current_chat,
        switch_inline_query_chosen_chat=button.switch_inline_query_chosen_chat,
        copy_text=button.copy_text,
        disabled=button.disabled,
    )


def inline_keyboard_to_rich_blocks(
    markup: InlineKeyboardMarkup,
    *,
    align: ButtonAlignment = "center",
) -> list[InputRichBlockButtons] | None:
    """Convert an inline keyboard to one rich-message button block per row.

    Returns ``None`` when a keyboard uses a Bot API button type that rich
    messages cannot represent, allowing callers to keep the original keyboard
    rather than silently dropping or changing any controls.
    """
    if align not in {"left", "center", "right"}:
        raise ValueError("Button alignment must be left, center, or right")
    if markup.force_reply:
        return None

    blocks: list[InputRichBlockButtons] = []
    for row in markup.inline_keyboard:
        if not row:
            continue
        if len(row) > 8:
            return None
        converted = [_to_rich_button(button) for button in row]
        if any(button is None for button in converted):
            return None
        blocks.append(
            rich_button_row(
                [button for button in converted if button is not None],
                align=align,
            )
        )
    return blocks or None


def embed_inline_keyboard(
    content: str | InputRichMessage,
    markup: InlineKeyboardMarkup | None,
    *,
    align: ButtonAlignment = "center",
) -> tuple[str | InputRichMessage, InlineKeyboardMarkup | None]:
    """Embed supported controls in rich content as aligned button rows.

    Plain text is wrapped in a literal paragraph when its keyboard can be
    represented as rich buttons. Rich content keeps its existing blocks.
    Unsupported keyboard button types and non-block rich content retain their
    original ``reply_markup`` as a compatibility fallback.
    """
    if isinstance(content, str):
        if markup is None or not content:
            return content, markup
        paragraph_content = InputRichMessage(
            blocks=[InputRichBlockParagraph(text=content)]
        )
        enriched, remaining_markup = embed_inline_keyboard(
            paragraph_content, markup, align=align
        )
        if remaining_markup is None and isinstance(enriched, InputRichMessage):
            return enriched, None
        return content, markup

    if (
        markup is None
        or content.blocks is None
        or content.html is not None
        or content.markdown is not None
    ):
        return content, markup
    rich_blocks = inline_keyboard_to_rich_blocks(markup, align=align)
    if rich_blocks is None:
        return content, markup

    enriched = InputRichMessage(
        blocks=[*content.blocks, *rich_blocks],
        is_rtl=content.is_rtl,
        skip_entity_detection=content.skip_entity_detection,
        media=content.media,
    )
    return enriched, None
