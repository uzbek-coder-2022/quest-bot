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

from ..database import Database
from ..keyboards import button, support_start_keyboard, ticket_reply_keyboard
from ..localization import tr
from ..states import SupportFlow

router = Router(name="support")


async def _ensure_private(callback: CallbackQuery, db: Database) -> bool:
    if callback.message and callback.message.chat.type == "private":
        return True
    language = await db.get_language(callback.from_user.id)
    await callback.answer(tr(language, "open_private_chat"), show_alert=True)
    return False


def _tickets_keyboard(language: str, tickets: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for ticket in tickets:
        label = f"#{ticket['id']} · {tr(language, 'ticket_open' if ticket['status'] == 'open' else 'ticket_closed')}"
        rows.append([button(label, f"support:ticket:{ticket['id']}")])
    rows.append([button(tr(language, "btn_home"), "menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _notify_admins(message: Message, db: Database, ticket_id: int, text: str) -> None:
    ticket = await db.get_ticket(ticket_id)
    if not ticket:
        return
    user = await db.get_user(int(ticket["user_id"])) or {}
    sender_name = user.get("full_name") or user.get("username") or str(ticket["user_id"])
    for admin_id in await db.support_admin_recipients(ticket_id):
        language = await db.get_language(admin_id)
        notice = tr(language, "support_notification", ticket=ticket_id, user=sender_name, message=text)
        try:
            await message.bot.send_message(admin_id, notice, reply_markup=ticket_reply_keyboard(language, ticket_id))
        except TelegramAPIError:
            continue


@router.message(Command("support"))
async def support_command(message: Message, db: Database) -> None:
    language = await db.get_language(message.from_user.id if message.from_user else 0)
    if message.chat.type != "private":
        await message.answer(tr(language, "open_private_chat"))
        return
    quests = await db.support_quests_for_user(message.from_user.id) if message.from_user else []
    text = tr(language, "support_choose")
    if not quests:
        text += f"\n\n{tr(language, 'support_no_quests')}"
    await message.answer(text, reply_markup=support_start_keyboard(language, quests))


@router.callback_query(F.data == "support:open")
async def open_support(callback: CallbackQuery, db: Database) -> None:
    if not await _ensure_private(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    quests = await db.support_quests_for_user(callback.from_user.id)
    text = tr(language, "support_choose")
    if not quests:
        text += f"\n\n{tr(language, 'support_no_quests')}"
    if callback.message:
        await callback.message.edit_text(text, reply_markup=support_start_keyboard(language, quests))
    await callback.answer()


@router.callback_query(F.data == "support:new:super")
async def new_superadmin_ticket(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not await _ensure_private(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    await state.update_data(support_quest_id=None, support_target_admin_id=None)
    await state.set_state(SupportFlow.new_message)
    if callback.message:
        await callback.message.answer(tr(language, "support_ask"))
    await callback.answer()


@router.callback_query(F.data.startswith("support:new:quest:"))
async def new_quest_admin_ticket(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not await _ensure_private(callback, db):
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    quests = await db.support_quests_for_user(callback.from_user.id)
    quest = next((item for item in quests if int(item["id"]) == quest_id), None)
    language = await db.get_language(callback.from_user.id)
    if not quest:
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    await state.update_data(support_quest_id=quest_id, support_target_admin_id=int(quest["owner_id"]))
    await state.set_state(SupportFlow.new_message)
    if callback.message:
        await callback.message.answer(tr(language, "support_ask"))
    await callback.answer()


@router.message(SupportFlow.new_message)
async def create_ticket_message(message: Message, state: FSMContext, db: Database) -> None:
    language = await db.get_language(message.from_user.id)
    text = (message.text or "").strip()
    if not text or len(text) > 2000:
        await message.answer(tr(language, "invalid_text"))
        return
    data = await state.get_data()
    ticket_id = await db.create_ticket(
        message.from_user.id,
        data.get("support_quest_id"),
        data.get("support_target_admin_id"),
        text,
    )
    await db.log_action(message.from_user.id, "support.ticket.created", "support_ticket", ticket_id)
    await state.clear()
    await message.answer(tr(language, "support_created"), reply_markup=ticket_reply_keyboard(language, ticket_id))
    await _notify_admins(message, db, ticket_id, text)


@router.callback_query(F.data == "support:tickets")
async def list_my_tickets(callback: CallbackQuery, db: Database) -> None:
    if not await _ensure_private(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    tickets = await db.user_tickets(callback.from_user.id)
    text = tr(language, "btn_my_tickets")
    if not tickets:
        text += f"\n\n{tr(language, 'no_tickets')}"
    if callback.message:
        await callback.message.edit_text(text, reply_markup=_tickets_keyboard(language, tickets))
    await callback.answer()


@router.callback_query(F.data.startswith("support:ticket:"))
async def show_ticket_history(callback: CallbackQuery, db: Database) -> None:
    if not await _ensure_private(callback, db):
        return
    try:
        ticket_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    ticket = await db.get_ticket(ticket_id)
    language = await db.get_language(callback.from_user.id)
    if not ticket or int(ticket["user_id"]) != callback.from_user.id:
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    messages = await db.ticket_messages(ticket_id)
    content = "\n\n".join(f"{item['sender_id']}: {item['message']}" for item in messages)
    text = tr(
        language,
        "ticket_history",
        ticket=ticket_id,
        status=tr(language, "ticket_open" if ticket["status"] == "open" else "ticket_closed"),
        messages=content or "—",
    )
    if callback.message:
        await callback.message.edit_text(text[:4000], reply_markup=ticket_reply_keyboard(language, ticket_id))
    await callback.answer()


@router.callback_query(F.data.startswith("support:reply:"))
async def begin_ticket_reply(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not await _ensure_private(callback, db):
        return
    try:
        ticket_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    ticket = await db.get_ticket(ticket_id)
    language = await db.get_language(callback.from_user.id)
    if not ticket or ticket["status"] != "open":
        await callback.answer(tr(language, "error_generic"), show_alert=True)
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
        await callback.message.answer(tr(language, "support_reply_prompt", ticket=ticket_id))
    await callback.answer()


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
        await message.answer(tr(language, "error_generic"))
        return
    await state.clear()
    await message.answer(tr(language, "support_created"))
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
    if not ticket or not await db.add_ticket_message(ticket_id, message.from_user.id, text):
        await state.clear()
        await message.answer(tr(language, "error_generic"))
        return
    await state.clear()
    user_language = ticket["language"]
    try:
        await message.bot.send_message(
            int(ticket["user_id"]),
            tr(user_language, "support_reply_to_user", ticket=ticket_id, message=text),
            reply_markup=ticket_reply_keyboard(user_language, ticket_id),
        )
    except TelegramAPIError:
        pass
    await message.answer(tr(language, "support_created"))
