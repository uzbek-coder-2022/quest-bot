"""Start, language, menu, and help handlers."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..config import Settings
from .. import navigation
from ..database import Database
from ..keyboards import (
    admin_home_keyboard,
    guide_keyboard,
    join_confirmation_keyboard,
    language_keyboard,
    main_menu,
)
from ..localization import LANGUAGES, tr
from ..presentation import (
    copy_quest_cover,
    guide_message,
    information_message,
    quest_preview,
)
from ..services import delete_archived_message
from ..utils import safe_edit

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
    token: str | None = None
    quest_id: int | None = None
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

    language = await db.get_language(user.id)
    quest = (
        await db.get_quest_by_token(quest_id, token)
        if token is not None
        else await db.get_quest(quest_id)
    )
    if not quest:
        await message.answer(
            tr(
                language,
                "private_link_invalid" if token is not None else "quest_not_found",
            )
        )
        return True
    if quest["visibility"] == "private" and token is None:
        await message.answer(tr(language, "private_link_invalid"))
        return True

    banned, reason = await db.is_globally_banned(user.id)
    if banned:
        suffix = tr(language, "reason_line", reason=reason) if reason else ""
        await message.answer(tr(language, "global_blocked", reason=suffix))
        return True
    if quest.get("paused_at"):
        await message.answer(tr(language, "quest_paused_notice"))
        return True
    if quest["status"] not in {"scheduled", "active"}:
        await message.answer(tr(language, "join_closed"))
        return True

    participant = await db.participant(quest_id, user.id)
    if participant:
        if participant["status"] == "blocked":
            suffix = (
                tr(language, "reason_line", reason=participant.get("ban_reason"))
                if participant.get("ban_reason")
                else ""
            )
            await message.answer(tr(language, "blocked_notice", reason=suffix))
        else:
            await message.answer(tr(language, "already_joined"))
        return True

    participant_count = await db.participant_count(quest_id)
    if not quest.get("cover_file_id"):
        await copy_quest_cover(bot, quest, user.id)
    await message.answer_rich(
        quest_preview(quest, language, participant_count),
        reply_markup=join_confirmation_keyboard(language, quest_id, token),
    )
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
        language = await db.get_language(
            message.from_user.id if message.from_user else 0
        )
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
    await message.answer_rich(
        information_message(
            f"👋 {tr(language, 'welcome')}",
            f"✨ {tr(language, 'welcome_hint')}\n\n📋 {tr(language, 'menu')}",
        ),
        reply_markup=main_menu(language, role),
    )


@router.message(Command("cancel"))
async def cancel_command(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    if message.from_user:
        for stage in [*data.get("stages", []), data.get("stage_draft", {})]:
            await delete_archived_message(
                message.bot, stage.get("source_chat_id"), stage.get("source_message_id")
            )
        await delete_archived_message(
            message.bot, data.get("cover_chat_id"), data.get("cover_message_id")
        )
    language = await db.get_language(message.from_user.id if message.from_user else 0)
    role = await db.get_role(message.from_user.id) if message.from_user else None
    await message.answer(
        tr(language, "cancelled"),
        reply_markup=admin_home_keyboard(language)
        if role in {"admin", "superadmin"}
        else None,
    )


@router.message(Command("menu"))
async def menu_command(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    await _register_message_user(message, db)
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    role = await db.get_role(message.from_user.id)
    await message.answer_rich(
        information_message(f"📋 {tr(language, 'menu')}"),
        reply_markup=main_menu(language, role),
    )


@router.message(Command("language"))
async def language_command(message: Message, db: Database) -> None:
    await _register_message_user(message, db)
    language = await db.get_language(message.from_user.id if message.from_user else 0)
    await message.answer(
        tr(language, "language_choose"), reply_markup=language_keyboard(language)
    )


@router.message(Command("help"))
async def help_command(message: Message, db: Database) -> None:
    await _register_message_user(message, db)
    language = await db.get_language(message.from_user.id if message.from_user else 0)
    role = await db.get_role(message.from_user.id) if message.from_user else None
    await message.answer_rich(
        guide_message(language), reply_markup=guide_keyboard(language, role)
    )


@router.callback_query(F.data == "menu:home")
async def home_callback(
    callback: CallbackQuery, db: Database, state: FSMContext
) -> None:
    await state.clear()
    navigation.clear(callback.from_user.id)
    if not callback.from_user:
        return
    language = await db.get_language(callback.from_user.id)
    role = await db.get_role(callback.from_user.id)
    if callback.message:
        await safe_edit(
            callback,
            information_message(f"📋 {tr(language, 'menu')}"),
            reply_markup=main_menu(language, role),
        )
    await callback.answer()


@router.callback_query(F.data == "menu:guide")
async def guide_callback(callback: CallbackQuery, db: Database) -> None:
    language = await db.get_language(callback.from_user.id)
    if callback.message:
        await safe_edit(
            callback,
            guide_message(language),
            reply_markup=guide_keyboard(
                language, await db.get_role(callback.from_user.id)
            ),
        )
    await callback.answer()


@router.callback_query(F.data == "menu:language")
async def choose_language_callback(callback: CallbackQuery, db: Database) -> None:
    language = await db.get_language(callback.from_user.id)
    if callback.message:
        await callback.message.edit_text(
            tr(language, "language_choose"), reply_markup=language_keyboard(language)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("lang:"))
async def set_language_callback(callback: CallbackQuery, db: Database) -> None:
    language = callback.data.split(":", 1)[1]
    if language not in LANGUAGES:
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
        language = await db.get_language(
            message.from_user.id if message.from_user else 0
        )
        await message.answer(tr(language, "ask_chat_id"))
        return
    language = await db.get_language(message.from_user.id if message.from_user else 0)
    await message.answer(
        tr(
            language,
            "chat_id_result",
            chat_id=message.chat.id,
            title=message.chat.title or "Chat",
        )
    )
