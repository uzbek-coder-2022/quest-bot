"""Start, language, menu, and help handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..config import Settings
from ..database import Database
from ..keyboards import language_keyboard, main_menu
from ..localization import tr
from ..services import get_chat_invite_for_participant, send_current_stage_after_join

router = Router(name="common")
logger = logging.getLogger(__name__)


async def _register_message_user(message: Message, db: Database) -> None:
    user = message.from_user
    if user:
        full_name = " ".join(part for part in (user.first_name, user.last_name) if part)
        await db.ensure_user(user.id, user.username, full_name)


async def _join_from_payload(
    message: Message, payload: str, db: Database, bot: Bot
) -> bool:
    user = message.from_user
    if not user:
        return False
    token = None
    quest_id = None
    try:
        if payload.startswith("play_"):
            quest_id = int(payload.removeprefix("play_"))
        elif payload.startswith("q_"):
            _, raw_id, token = payload.split("_", 2)
            quest_id = int(raw_id)
        else:
            return False
    except (ValueError, TypeError):
        return False

    joined = await db.join_quest(quest_id, user.id, token, datetime.now(timezone.utc).replace(microsecond=0).isoformat())
    language = await db.get_language(user.id)
    code = joined["code"]
    if code == "not_found":
        await message.answer(tr(language, "quest_not_found"))
        return True
    quest = joined.get("quest")
    if code == "invalid_token":
        await message.answer(tr(language, "private_link_invalid"))
        return True
    if code == "globally_banned":
        reason = joined.get("reason")
        suffix = tr(language, "reason_line", reason=reason) if reason else ""
        await message.answer(tr(language, "global_blocked", reason=suffix))
        return True
    if code == "blocked":
        participant = await db.participant(quest_id, user.id)
        reason = participant.get("ban_reason") if participant else None
        suffix = tr(language, "reason_line", reason=reason) if reason else ""
        await message.answer(tr(language, "blocked_notice", reason=suffix))
        return True
    if code == "closed":
        await message.answer(tr(language, "join_closed"))
        return True
    if code == "already_joined":
        await message.answer(tr(language, "already_joined"))
        return True

    await message.answer(tr(language, "join_success"))
    if quest and quest.get("chat_id"):
        if quest["status"] == "active":
            invite_link = await get_chat_invite_for_participant(bot, db, quest, user.id)
            await message.answer(
                tr(language, "invite_link_ready", link=invite_link)
                if invite_link
                else tr(language, "invite_unavailable")
            )
        else:
            await message.answer(tr(language, "invite_at_start"))
    if quest and quest["status"] == "active":
        bot_identity = await bot.get_me()
        await send_current_stage_after_join(bot, db, quest, user.id, bot_identity.username or "")
    return True


@router.message(CommandStart())
async def start_command(
    message: Message,
    command: CommandObject,
    db: Database,
    settings: Settings,
    bot: Bot,
    state: FSMContext,
) -> None:
    if message.chat.type != "private":
        await _register_message_user(message, db)
        language = await db.get_language(message.from_user.id if message.from_user else 0)
        await message.answer(tr(language, "open_private_chat"))
        return
    await state.clear()
    await _register_message_user(message, db)
    user = message.from_user
    if not user:
        return
    language = await db.get_language(user.id)
    banned, reason = await db.is_globally_banned(user.id)
    if banned:
        suffix = tr(language, "reason_line", reason=reason) if reason else ""
        await message.answer(tr(language, "global_blocked", reason=suffix))
        return
    if command.args:
        handled = await _join_from_payload(message, command.args.strip(), db, bot)
        if handled:
            return
    role = await db.get_role(user.id)
    await message.answer(
        f"{tr(language, 'welcome')}\n\n{tr(language, 'welcome_hint')}\n\n{tr(language, 'menu')}",
        reply_markup=main_menu(language, role),
    )


@router.message(Command("cancel"))
async def cancel_command(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    language = await db.get_language(message.from_user.id if message.from_user else 0)
    await message.answer(tr(language, "cancelled"))


@router.message(Command("menu"))
async def menu_command(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    await _register_message_user(message, db)
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    role = await db.get_role(message.from_user.id)
    await message.answer(tr(language, "menu"), reply_markup=main_menu(language, role))


@router.message(Command("language"))
async def language_command(message: Message, db: Database) -> None:
    await _register_message_user(message, db)
    language = await db.get_language(message.from_user.id if message.from_user else 0)
    await message.answer(tr(language, "language_choose"), reply_markup=language_keyboard())


@router.message(Command("help"))
async def help_command(message: Message, db: Database) -> None:
    await _register_message_user(message, db)
    language = await db.get_language(message.from_user.id if message.from_user else 0)
    await message.answer(tr(language, "guide"), reply_markup=main_menu(language, await db.get_role(message.from_user.id) if message.from_user else None))


@router.callback_query(F.data == "menu:home")
async def home_callback(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    if not callback.from_user:
        return
    language = await db.get_language(callback.from_user.id)
    role = await db.get_role(callback.from_user.id)
    if callback.message:
        await callback.message.edit_text(tr(language, "menu"), reply_markup=main_menu(language, role))
    await callback.answer()


@router.callback_query(F.data == "menu:guide")
async def guide_callback(callback: CallbackQuery, db: Database) -> None:
    language = await db.get_language(callback.from_user.id)
    if callback.message:
        await callback.message.edit_text(tr(language, "guide"), reply_markup=main_menu(language, await db.get_role(callback.from_user.id)))
    await callback.answer()


@router.callback_query(F.data == "menu:language")
async def choose_language_callback(callback: CallbackQuery, db: Database) -> None:
    language = await db.get_language(callback.from_user.id)
    if callback.message:
        await callback.message.edit_text(tr(language, "language_choose"), reply_markup=language_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("lang:"))
async def set_language_callback(callback: CallbackQuery, db: Database) -> None:
    language = callback.data.split(":", 1)[1]
    if language not in {"uz", "ru", "en"}:
        await callback.answer("Invalid language", show_alert=True)
        return
    await db.set_language(callback.from_user.id, language)
    role = await db.get_role(callback.from_user.id)
    if callback.message:
        await callback.message.edit_text(
            f"{tr(language, 'language_saved')}\n\n{tr(language, 'menu')}",
            reply_markup=main_menu(language, role),
        )
    await callback.answer()


@router.message(Command("chatid"))
async def chat_id_command(message: Message, db: Database) -> None:
    if message.chat.type == "private":
        language = await db.get_language(message.from_user.id if message.from_user else 0)
        await message.answer(tr(language, "ask_chat_id"))
        return
    language = await db.get_language(message.from_user.id if message.from_user else 0)
    await message.answer(tr(language, "chat_id_result", chat_id=message.chat.id, title=message.chat.title or "Chat"))
