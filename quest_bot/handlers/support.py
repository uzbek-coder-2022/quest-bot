"""Private ticket-style chat between users and the right administrators."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)

from .. import navigation
from ..database import Database
from ..keyboards import button, support_start_keyboard, ticket_reply_keyboard
from ..localization import tr
from ..presentation import (
    information_message,
    support_history,
    support_notification,
    support_reply,
    thread_tag,
)
from ..states import SupportFlow
from ..rich_text import heading, paragraph, rich_message
from ..utils import safe_edit

router = Router(name="support")


async def _ensure_private(callback: CallbackQuery, db: Database) -> bool:
    if callback.message and callback.message.chat.type == "private":
        return True
    language = await db.get_language(callback.from_user.id)
    await callback.answer(tr(language, "open_private_chat"), show_alert=True)
    return False


def _tickets_keyboard(
    language: str,
    tickets: list[dict],
    page: int = 0,
    page_size: int = 10,
    back_target: str = "support:open",
) -> InlineKeyboardMarkup:
    rows = []
    for ticket in tickets:
        label = f"#{ticket['id']} · {tr(language, 'ticket_open' if ticket['status'] == 'open' else 'ticket_closed')}"
        rows.append(
            [button(label, f"support:ticket:{ticket['id']}:{max(0, page)}")]
        )
    nav = []
    if page > 0:
        nav.append(button("⬅️", f"support:tickets:{page - 1}"))
    if len(tickets) >= max(1, page_size):
        nav.append(button("➡️", f"support:tickets:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([button(tr(language, "btn_back"), back_target)])
    rows.append([button(tr(language, "btn_home"), "menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _tagged(language: str, key: str, ticket_id: int, **kwargs: object) -> str:
    """A localized confirmation line followed by the searchable thread tag."""
    return f"{tr(language, key, **kwargs)}\n{thread_tag(ticket_id)}"


async def _notify_admins(
    message: Message, db: Database, ticket_id: int, text: str
) -> None:
    ticket = await db.get_ticket(ticket_id)
    if not ticket:
        return
    user = await db.get_user(int(ticket["user_id"])) or {}
    sender_name = (
        user.get("full_name") or user.get("username") or str(ticket["user_id"])
    )
    for admin_id in await db.support_admin_recipients(ticket_id):
        language = await db.get_language(admin_id)
        if ticket.get("target_admin_id"):
            source = tr(
                language,
                "support_source_quest_admin",
                quest=ticket.get("quest_title") or "—",
            )
        else:
            source = tr(language, "support_source_superadmin")
        notice = support_notification(ticket_id, source, sender_name, text)
        try:
            await message.bot.send_rich_message(
                admin_id,
                notice,
                reply_markup=ticket_reply_keyboard(language, ticket_id),
            )
        except TelegramAPIError:
            continue


@router.message(Command("support"))
async def support_command(message: Message, db: Database) -> None:
    language = await db.get_language(message.from_user.id if message.from_user else 0)
    if message.chat.type != "private":
        await message.answer(tr(language, "open_private_chat"))
        return
    if not message.from_user:
        return
    page_size = int(await db.settings_get("page_size", "10"))
    quests = await db.support_quests_for_user(message.from_user.id, 0, page_size)
    await message.answer_rich(
        information_message(
            tr(language, "support_choose"),
            None if quests else tr(language, "support_no_quests"),
        ),
        reply_markup=support_start_keyboard(language, quests, 0, page_size),
    )


async def _show_support_page(
    callback: CallbackQuery, db: Database, page: int
) -> None:
    """Show one page of quests a user can contact their administrator about."""
    language = await db.get_language(callback.from_user.id)
    navigation.remember(callback.from_user.id, support_page=max(0, page))
    page_size = int(await db.settings_get("page_size", "10"))
    total = await db.support_quest_count(callback.from_user.id)
    last_page = max(0, (total - 1) // max(1, page_size))
    page = min(max(0, page), last_page)
    quests = await db.support_quests_for_user(callback.from_user.id, page, page_size)
    footer = None
    if total > page_size:
        start = page * page_size
        footer = f"{start + 1}–{min(start + len(quests), total)}/{total}"
    if callback.message:
        await safe_edit(
            callback,
            information_message(
                tr(language, "support_choose"),
                None if quests else tr(language, "support_no_quests"),
                footer,
            ),
            reply_markup=support_start_keyboard(language, quests, page, page_size),
        )
    await callback.answer()


@router.callback_query(F.data == "support:open")
async def open_support(callback: CallbackQuery, db: Database) -> None:
    if not await _ensure_private(callback, db):
        return
    await _show_support_page(callback, db, 0)


@router.callback_query(F.data.startswith("support:open:"))
async def open_support_page(callback: CallbackQuery, db: Database) -> None:
    if not await _ensure_private(callback, db):
        return
    try:
        page = max(0, int(callback.data.rsplit(":", 1)[1]))
    except (ValueError, AttributeError):
        await callback.answer()
        return
    await _show_support_page(callback, db, page)


@router.callback_query(F.data == "support:new:super")
async def new_superadmin_ticket(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    if not await _ensure_private(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    await state.update_data(support_quest_id=None, support_target_admin_id=None)
    await state.set_state(SupportFlow.new_message)
    if callback.message:
        await callback.message.answer(tr(language, "support_ask"))
    await callback.answer()


@router.callback_query(F.data.startswith("support:new:quest:"))
async def new_quest_admin_ticket(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    if not await _ensure_private(callback, db):
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    quest = await db.support_quest_for_user(quest_id, callback.from_user.id)
    language = await db.get_language(callback.from_user.id)
    if not quest:
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    await state.update_data(
        support_quest_id=quest_id, support_target_admin_id=int(quest["owner_id"])
    )
    await state.set_state(SupportFlow.new_message)
    if callback.message:
        await callback.message.answer(tr(language, "support_ask"))
    await callback.answer()


async def _submit_support_ticket(
    message: Message, state: FSMContext, db: Database, user_id: int, text: str
) -> None:
    """Create the ticket for an already validated support message."""
    data = await state.get_data()
    ticket_id = await db.create_ticket(
        user_id,
        data.get("support_quest_id"),
        data.get("support_target_admin_id"),
        text,
    )
    await db.log_action(
        user_id, "support.ticket.created", "support_ticket", ticket_id
    )
    language = await db.get_language(user_id)
    await state.clear()
    await message.answer(
        _tagged(language, "support_created", ticket_id)
        + "\n"
        + tr(language, "support_thread_started", ticket=ticket_id)
    )
    await _notify_admins(message, db, ticket_id, text)


@router.message(SupportFlow.new_message)
async def create_ticket_message(
    message: Message, state: FSMContext, db: Database
) -> None:
    language = await db.get_language(message.from_user.id)
    text = (message.text or "").strip()
    if not text or len(text) > 2000:
        await message.answer(tr(language, "invalid_text"))
        return
    # A participant may write their answer while the support form is open. Ask
    # which one they meant instead of silently sending the answer as a ticket.
    stages = await db.open_stages_for_user(message.from_user.id)
    if stages:
        await state.update_data(pending_support_text=text)
        await message.answer(
            information_message(
                tr(language, "ambiguous_message_title"),
                tr(language, "ambiguous_message_body"),
            ),
            reply_markup=answer_or_ticket_keyboard(language, stages),
        )
        return
    await _submit_support_ticket(message, state, db, message.from_user.id, text)


@router.callback_query(F.data == "support:pending:ticket")
async def pending_text_as_ticket(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    """Treat the stored text as a support message after all."""
    if not await _ensure_private(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    data = await state.get_data()
    text = str(data.get("pending_support_text") or "").strip()
    if not text:
        await state.clear()
        await callback.answer(tr(language, "error_generic"), show_alert=True)
        return
    await _submit_support_ticket(
        callback.message, state, db, callback.from_user.id, text
    )
    await callback.answer()


async def _show_tickets_page(
    callback: CallbackQuery, db: Database, page: int
) -> None:
    """Show one page of a user's support tickets, ten per page by default."""
    language = await db.get_language(callback.from_user.id)
    navigation.remember(callback.from_user.id, tickets_page=max(0, page))
    page_size = int(await db.settings_get("page_size", "10"))
    tickets, total, page = await db.user_tickets_page(
        callback.from_user.id, page, page_size
    )
    footer = None
    if total > page_size:
        start = page * page_size
        footer = f"{start + 1}–{min(start + len(tickets), total)}/{total}"
    if callback.message:
        await safe_edit(
            callback,
            information_message(
                tr(language, "btn_my_tickets"),
                None if tickets else tr(language, "no_tickets"),
                footer,
            ),
            reply_markup=_tickets_keyboard(
                language,
                tickets,
                page,
                page_size,
                back_target=(
                    "support:open:"
                    f"{navigation.recall(callback.from_user.id, 'support_page', 0)}"
                ),
            ),
        )
    await callback.answer()


@router.callback_query(F.data == "support:tickets")
async def list_my_tickets(callback: CallbackQuery, db: Database) -> None:
    if not await _ensure_private(callback, db):
        return
    await _show_tickets_page(callback, db, 0)


@router.callback_query(F.data.startswith("support:tickets:"))
async def list_my_tickets_page(callback: CallbackQuery, db: Database) -> None:
    if not await _ensure_private(callback, db):
        return
    try:
        page = max(0, int(callback.data.rsplit(":", 1)[1]))
    except (ValueError, AttributeError):
        await callback.answer()
        return
    await _show_tickets_page(callback, db, page)


@router.callback_query(F.data.startswith("support:ticket:"))
async def show_ticket_history(callback: CallbackQuery, db: Database) -> None:
    if not await _ensure_private(callback, db):
        return
    try:
        parts = callback.data.split(":")
        ticket_id = int(parts[2])
        list_page = max(0, int(parts[3])) if len(parts) > 3 else 0
    except (ValueError, IndexError):
        await callback.answer()
        return
    ticket = await db.get_ticket(ticket_id)
    language = await db.get_language(callback.from_user.id)
    if not ticket or int(ticket["user_id"]) != callback.from_user.id:
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    messages = await db.ticket_messages(ticket_id)
    labelled_messages: list[tuple[str, str]] = []
    for item in messages:
        sender_id = int(item["sender_id"])
        if sender_id == int(ticket["user_id"]):
            sender_label = tr(language, "support_you")
        elif ticket.get("target_admin_id") and sender_id == int(
            ticket["target_admin_id"]
        ):
            sender_label = tr(
                language,
                "support_source_quest_admin",
                quest=ticket.get("quest_title") or "—",
            )
        elif await db.get_role(sender_id) == "superadmin":
            sender_label = tr(language, "support_source_superadmin")
        else:
            sender_label = tr(language, "role_admin")
        message_text = str(item["message"])
        if len(message_text) > 250:
            message_text = message_text[:247] + "..."
        labelled_messages.append((sender_label, message_text))
    labelled_messages = labelled_messages[-10:]
    header = tr(
        language,
        "ticket_history",
        ticket=ticket_id,
        status=tr(
            language, "ticket_open" if ticket["status"] == "open" else "ticket_closed"
        ),
        messages="",
    ).strip()
    header = f"{header} · {thread_tag(ticket_id)}"
    history = support_history(labelled_messages, header, "—")
    if callback.message:
        await safe_edit(
            callback,
            history,
            reply_markup=ticket_reply_keyboard(
                language,
                ticket_id,
                back_target=f"support:tickets:{list_page}",
                can_reply=ticket["status"] == "open",
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("support:reply:"))
async def begin_ticket_reply(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    if not await _ensure_private(callback, db):
        return
    try:
        ticket_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    ticket = await db.get_ticket(ticket_id)
    language = await db.get_language(callback.from_user.id)
    if not ticket:
        await callback.answer(tr(language, "error_generic"), show_alert=True)
        return
    if ticket["status"] != "open":
        await callback.answer(
            tr(language, "support_ticket_closed", ticket=ticket_id), show_alert=True
        )
        return
    if int(ticket["user_id"]) == callback.from_user.id:
        await state.update_data(ticket_id=ticket_id)
        await state.set_state(SupportFlow.user_reply)
    elif callback.from_user.id in await db.support_admin_recipients(ticket_id):
        await state.update_data(ticket_id=ticket_id)
        await state.set_state(SupportFlow.admin_reply)
    else:
        await callback.answer(tr(language, "admin_only"), show_alert=True)
        return
    if callback.message:
        # The message that was answered no longer offers Reply, so the same
        # notification cannot be answered twice.
        try:
            await callback.message.edit_reply_markup(
                reply_markup=ticket_reply_keyboard(
                    language, ticket_id, can_reply=False
                )
            )
        except TelegramAPIError:
            pass
        await callback.message.answer(
            tr(language, "support_reply_prompt", ticket=ticket_id)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("support:close:"))
async def close_ticket_callback(
    callback: CallbackQuery, db: Database
) -> None:
    """Close one conversation so it stops waiting for a reply."""
    if not await _ensure_private(callback, db):
        return
    try:
        ticket_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.answer()
        return
    ticket = await db.get_ticket(ticket_id)
    language = await db.get_language(callback.from_user.id)
    if not ticket:
        await callback.answer(tr(language, "error_generic"), show_alert=True)
        return
    recipients = await db.support_admin_recipients(ticket_id)
    is_user = int(ticket["user_id"]) == callback.from_user.id
    if not is_user and callback.from_user.id not in recipients:
        await callback.answer(tr(language, "admin_only"), show_alert=True)
        return
    if not await db.close_ticket(ticket_id, callback.from_user.id):
        await callback.answer(
            tr(language, "support_ticket_closed", ticket=ticket_id), show_alert=True
        )
        return
    if callback.message:
        await safe_edit(
            callback,
            rich_message(
                heading(
                    tr(language, "support_thread_closed_title", ticket=ticket_id),
                    size=2,
                ),
                paragraph(tr(language, "support_thread_closed_body")),
                paragraph(thread_tag(ticket_id)),
            ),
        )
    # Tell the other side that this conversation is over.
    notices: list[int] = []
    if is_user:
        notices = recipients
    else:
        notices = [int(ticket["user_id"])]
    for target_id in notices:
        target_language = await db.get_language(target_id)
        try:
            await callback.bot.send_message(
                target_id,
                f"{tr(target_language, 'support_thread_closed_notice', ticket=ticket_id)}\n"
                f"{thread_tag(ticket_id)}",
            )
        except TelegramAPIError:
            continue
    await callback.answer(
        tr(language, "support_thread_closed_notice", ticket=ticket_id)
    )


@router.message(SupportFlow.user_reply)
async def user_ticket_reply(message: Message, state: FSMContext, db: Database) -> None:
    text = (message.text or "").strip()
    language = await db.get_language(message.from_user.id)
    if not text or len(text) > 2000:
        await message.answer(tr(language, "invalid_text"))
        return
    data = await state.get_data()
    ticket_id = int(data["ticket_id"])
    if not await db.add_ticket_message(ticket_id, message.from_user.id, text):
        await state.clear()
        await message.answer(
            tr(language, "support_ticket_closed", ticket=ticket_id)
        )
        return
    await state.clear()
    await message.answer(
        _tagged(language, "support_created", ticket_id)
        + "\n"
        + tr(language, "support_thread_started", ticket=ticket_id)
    )
    await _notify_admins(message, db, ticket_id, text)


@router.message(SupportFlow.admin_reply)
async def admin_ticket_reply(message: Message, state: FSMContext, db: Database) -> None:
    text = (message.text or "").strip()
    language = await db.get_language(message.from_user.id)
    if not text or len(text) > 2000:
        await message.answer(tr(language, "invalid_text"))
        return
    data = await state.get_data()
    ticket_id = int(data["ticket_id"])
    if message.from_user.id not in await db.support_admin_recipients(ticket_id):
        await state.clear()
        await message.answer(tr(language, "admin_only"))
        return
    ticket = await db.get_ticket(ticket_id)
    if not ticket or not await db.add_ticket_message(
        ticket_id, message.from_user.id, text
    ):
        await state.clear()
        await message.answer(
            tr(language, "support_ticket_closed", ticket=ticket_id)
        )
        return
    await state.clear()
    user_language = ticket["language"]
    if (
        ticket.get("target_admin_id")
        and int(ticket["target_admin_id"]) == message.from_user.id
    ):
        source = tr(
            user_language,
            "support_source_quest_admin",
            quest=ticket.get("quest_title") or "—",
        )
    else:
        source = tr(user_language, "support_source_superadmin")
    try:
        await message.bot.send_rich_message(
            int(ticket["user_id"]),
            support_reply(source, text, ticket_id),
            reply_markup=ticket_reply_keyboard(user_language, ticket_id),
        )
    except TelegramAPIError:
        pass
    await message.answer(
        _tagged(language, "support_admin_reply_sent", ticket_id),
        reply_markup=ticket_reply_keyboard(language, ticket_id, can_reply=False),
    )
