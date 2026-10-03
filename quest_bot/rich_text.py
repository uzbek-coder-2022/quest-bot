"""Small, safe constructors for Telegram Bot API rich-message blocks."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from aiogram.types import (
    InputRichBlockBlockQuotation,
    InputRichBlockDivider,
    InputRichBlockList,
    InputRichBlockListItem,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichMessage,
    RichTextBold,
    RichTextItalic,
)
from aiogram.types.input_rich_block_union import InputRichBlockUnion
from aiogram.types.rich_text_union import RichTextUnion


def bold(text: RichTextUnion) -> RichTextBold:
    """Mark text as bold without parsing user-controlled markup."""
    return RichTextBold(text=text)


def italic(text: RichTextUnion) -> RichTextItalic:
    """Mark text as italic without parsing user-controlled markup."""
    return RichTextItalic(text=text)


def heading(text: RichTextUnion, size: int = 2) -> InputRichBlockSectionHeading:
    """Build a native rich-message heading; size 1 is the largest."""
    if not 1 <= size <= 6:
        raise ValueError("Rich-message heading size must be between 1 and 6")
    return InputRichBlockSectionHeading(text=text, size=size)


def paragraph(text: RichTextUnion) -> InputRichBlockParagraph:
    """Build a paragraph while keeping all supplied strings as literal text."""
    return InputRichBlockParagraph(text=text)


def quote(*parts: RichTextUnion) -> InputRichBlockBlockQuotation:
    """Build a quotation block from literal text and/or typed rich-text nodes."""
    if not parts:
        raise ValueError("A quotation must contain text")
    content: RichTextUnion = parts[0] if len(parts) == 1 else list(parts)
    return InputRichBlockBlockQuotation(blocks=[paragraph(content)])


def divider() -> InputRichBlockDivider:
    """Build a visual separator between rich-message sections."""
    return InputRichBlockDivider()


def bullet_list(
    items: Sequence[RichTextUnion], *, ordered: bool = False
) -> InputRichBlockList:
    """Build a native list; each item can include bold or other typed text nodes."""
    list_items = [
        InputRichBlockListItem(
            blocks=[paragraph(item)],
            type="1" if ordered else None,
            value=index if ordered else None,
        )
        for index, item in enumerate(items, start=1)
    ]
    return InputRichBlockList(items=list_items)


def rich_message(*blocks: InputRichBlockUnion) -> InputRichMessage:
    """Create a structured rich message with literal, typed text content."""
    if not blocks:
        raise ValueError("A rich message must contain at least one block")
    return InputRichMessage(blocks=list(blocks))


def rich_list_message(
    title: str,
    lines: Iterable[str],
    empty_text: str | None = None,
) -> InputRichMessage:
    """Render a heading and a bulleted list, or an empty-state paragraph."""
    entries = [line for line in lines if line]
    blocks: list[InputRichBlockUnion] = [heading(title, size=1)]
    if entries:
        blocks.extend((divider(), bullet_list(entries)))
    elif empty_text:
        blocks.append(paragraph(empty_text))
    return rich_message(*blocks)
