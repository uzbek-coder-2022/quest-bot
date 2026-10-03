"""Public discovery, quest joining, answer submission, and rankings."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from ..database import Database, utc_now
from ..keyboards import (
    admin_quest_filters,
    answer_quest_selector,
    browse_filters,
    button,
    home_keyboard,
    manage_quest,
    quest_detail,
    review_keyboard,
)
from ..localization import tr
from ..services import (
    get_chat_invite_for_participant,
    send_current_stage_after_join,
    send_stage_to_user,
)
from ..states import AnswerFlow
from ..utils import (
    can_manage_quest,
    display_name,
    ensure_private_callback,
    format_datetime,
)

router = Router(name="quests")


def _status_name(language: str, status: str) -> str:
    return tr(language, f"status_{status}")


def _participant_status(language: str, status: str) -> str:
    key = {
        "joined": "participant_joined",
        "active": "participant_active",
        "completed": "participant_completed",
        "failed": "participant_failed",
        "blocked": "participant_blocked",
    }.get(status, "participant_joined")
    return tr(language, key)


async def _show_public_page(callback: CallbackQuery, db: Database, status: str, page: int) -> None:
    language = await db.get_language(callback.from_user.id)
    page_size = int(await db.settings_get("page_size", "10"))
    selected_status = None if status == "all" else status
    items = await db.list_public_quests(selected_status, max(0, page) * page_size, page_size)
    title = tr(language, "quests_title")
    if status != "all":
        title += f" · {tr(language, f'filter_{status}')}"
    text = title if items else f"{title}\n\n{tr(language, 'empty_quests')}"
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=browse_filters(language, status, max(0, page), items, page_size),
        )


async def _show_manage_page(callback: CallbackQuery, db: Database, status: str, page: int) -> None:
    if not await ensure_private_callback(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    role = await db.get_role(callback.from_user.id)
    if role not in {"admin", "superadmin"}:
        await callback.answer(tr(language, "admin_only"), show_alert=True)
        return
    page_size = int(await db.settings_get("page_size", "10"))
    selected_status = None if status == "all" else status
    items = await db.list_manageable_quests(
        callback.from_user.id,
        role == "superadmin",
        selected_status,
        max(0, page) * page_size,
        page_size,
    )
    title = tr(language, "my_quests_title")
    if not items:
        title += f"\n\n{tr(language, 'empty_quests')}"
    if callback.message:
        await callback.message.edit_text(
            title,
            reply_markup=admin_quest_filters(language, status, max(0, page), items, page_size),
        )


@router.message(Command("quests"))
async def quests_command(message: Message, db: Database) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    page_size = int(await db.settings_get("page_size", "10"))
    items = await db.list_public_quests(None, 0, page_size)
    title = tr(language, "quests_title")
    if not items:
        title += f"\n\n{tr(language, 'empty_quests')}"
    await message.answer(title, reply_markup=browse_filters(language, "all", 0, items, page_size))


@router.callback_query(F.data.startswith("browse:filter:"))
async def browse_filter_callback(callback: CallbackQuery, db: Database) -> None:
    try:
        _, _, status, page_text = callback.data.split(":", 3)
        page = int(page_text)
    except (ValueError, AttributeError):
        await callback.answer()
        return
    if status not in {"all", "scheduled", "active", "completed", "archived"}:
        await callback.answer()
        return
    await _show_public_page(callback, db, status, page)
    await callback.answer()


@router.callback_query(F.data.startswith("adminq:filter:"))
async def manage_filter_callback(callback: CallbackQuery, db: Database) -> None:
    try:
        _, _, status, page_text = callback.data.split(":", 3)
        page = int(page_text)
    except (ValueError, AttributeError):
        await callback.answer()
        return
    if status not in {"all", "scheduled", "active", "completed", "archived"}:
        await callback.answer()
        return
    await _show_manage_page(callback, db, status, page)
    await callback.answer()


@router.callback_query(F.data.startswith("quest:view:"))
async def view_quest_callback(callback: CallbackQuery, db: Database) -> None:
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    quest = await db.get_quest(quest_id)
    language = await db.get_language(callback.from_user.id)
    if not quest:
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    if quest["visibility"] == "private" and not await ensure_private_callback(callback, db):
        return
    participant = await db.participant(quest_id, callback.from_user.id)
    manager = await can_manage_quest(db, callback.from_user.id, quest)
    if quest["visibility"] == "private" and not participant and not manager:
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    duration_seconds = int(quest.get("duration_seconds") or 0)
    duration = "—" if duration_seconds == 0 else f"{duration_seconds // 60} min"
    chat_title = quest.get("chat_title") or ("Bot" if not quest.get("chat_id") else str(quest["chat_id"]))
    text = tr(
        language,
        "quest_details",
        title=quest["title"],
        description=quest["description"] or "—",
        status=_status_name(language, quest["status"]),
        visibility=tr(language, f"visibility_{quest['visibility']}"),
        start=format_datetime(quest["start_at"], language),
        stages=int(quest.get("stage_count", 0)),
        progression=tr(language, f"progression_{quest['progression']}"),
        duration=duration,
        chat=chat_title,
    )
    joined = bool(participant and participant["status"] != "blocked")
    if callback.message:
        await callback.message.edit_text(text, reply_markup=quest_detail(language, quest, joined))
    await callback.answer()


@router.callback_query(F.data.startswith("quest:join:"))
async def join_quest_callback(callback: CallbackQuery, db: Database, bot: Bot) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    quest = await db.get_quest(quest_id)
    if not quest or quest["visibility"] != "public":
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    result = await db.join_quest(quest_id, callback.from_user.id, None, utc_now())
    code = result["code"]
    if code == "globally_banned":
        reason = result.get("reason")
        suffix = tr(language, "reason_line", reason=reason) if reason else ""
        await callback.answer(tr(language, "global_blocked", reason=suffix), show_alert=True)
        return
    if code == "already_joined":
        await callback.answer(tr(language, "already_joined"), show_alert=True)
        return
    if code == "blocked":
        participant = await db.participant(quest_id, callback.from_user.id)
        suffix = tr(language, "reason_line", reason=participant.get("ban_reason")) if participant and participant.get("ban_reason") else ""
        await callback.answer(tr(language, "blocked_notice", reason=suffix), show_alert=True)
        return
    if code == "closed":
        await callback.answer(tr(language, "join_closed"), show_alert=True)
        return
    if code != "joined":
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    await db.log_action(callback.from_user.id, "participant.joined", "quest", quest_id)
    if callback.message:
        await callback.message.answer(tr(language, "join_success"))
    if quest.get("chat_id"):
        if quest["status"] == "active":
            invite_link = await get_chat_invite_for_participant(bot, db, quest, callback.from_user.id)
            await bot.send_message(
                callback.from_user.id,
                tr(language, "invite_link_ready", link=invite_link)
                if invite_link
                else tr(language, "invite_unavailable"),
            )
        else:
            await bot.send_message(callback.from_user.id, tr(language, "invite_at_start"))
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=quest_detail(language, quest, joined=True))
        except TelegramAPIError:
            pass
    if quest["status"] == "active":
        me = await bot.get_me()
        await send_current_stage_after_join(bot, db, quest, callback.from_user.id, me.username or "")
    await callback.answer()


@router.callback_query(F.data.startswith("quest:chatinvite:"))
async def participant_chat_invite(callback: CallbackQuery, db: Database, bot: Bot) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    quest = await db.get_quest(quest_id)
    participant = await db.participant(quest_id, callback.from_user.id)
    if not quest or quest["status"] != "active" or not quest.get("chat_id") or not participant or participant["status"] in {"blocked", "failed"}:
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    link = await get_chat_invite_for_participant(bot, db, quest, callback.from_user.id)
    if not link:
        await callback.answer(tr(language, "invite_unavailable"), show_alert=True)
        return
    if callback.message:
        await callback.message.answer(tr(language, "invite_link_ready", link=link))
    await callback.answer()


@router.callback_query(F.data.startswith("rating:show:"))
async def show_leaderboard(callback: CallbackQuery, db: Database) -> None:
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    quest = await db.get_quest(quest_id)
    if not quest:
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    if quest["visibility"] == "private" and not await ensure_private_callback(callback, db):
        return
    participant = await db.participant(quest_id, callback.from_user.id)
    if quest["visibility"] == "private" and not participant and not await can_manage_quest(db, callback.from_user.id, quest):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    rows = await db.leaderboard(quest_id)
    text = tr(language, "leaderboard_title", title=quest["title"])
    if not rows:
        text += f"\n\n{tr(language, 'leaderboard_empty')}"
    else:
        rendered = []
        for rank, item in enumerate(rows[:30], start=1):
            name = display_name(item.get("full_name"), item.get("username"), item.get("user_id"))
            rendered.append(
                tr(
                    language,
                    "leaderboard_row",
                    rank=rank,
                    name=name,
                    solved=item["solved"],
                    status=_participant_status(language, item["status"]),
                )
            )
        text += "\n\n" + "\n".join(rendered)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=home_keyboard(language))
    await callback.answer()


@router.callback_query(F.data.startswith("ratings:list:"))
async def leaderboard_quest_list(callback: CallbackQuery, db: Database) -> None:
    try:
        _, _, scope, page_text = callback.data.split(":", 3)
        page = int(page_text)
    except (ValueError, AttributeError):
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    size = int(await db.settings_get("page_size", "10"))
    if scope == "managed":
        if not await ensure_private_callback(callback, db):
            return
        role = await db.get_role(callback.from_user.id)
        if role not in {"admin", "superadmin"}:
            await callback.answer(tr(language, "admin_only"), show_alert=True)
            return
        quests = await db.list_manageable_quests(callback.from_user.id, role == "superadmin", None, page * size, size)
    else:
        quests = await db.list_public_quests(None, page * size, size)
    rows = [[button(item["title"][:50], f"rating:show:{item['id']}")] for item in quests]
    nav = []
    if page > 0:
        nav.append(button("◀", f"ratings:list:{scope}:{page - 1}"))
    if len(quests) == size:
        nav.append(button("▶", f"ratings:list:{scope}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([button(tr(language, "btn_home"), "menu:home")])
    text = tr(language, "btn_ratings")
    if not quests:
        text += f"\n\n{tr(language, 'empty_quests')}"
    if callback.message:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("manage:quest:"))
async def manage_quest_callback(callback: CallbackQuery, db: Database) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    quest = await db.get_quest(quest_id)
    language = await db.get_language(callback.from_user.id)
    role = await db.get_role(callback.from_user.id)
    if not quest or not await can_manage_quest(db, callback.from_user.id, quest):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    if callback.message:
        await callback.message.edit_text(quest["title"], reply_markup=manage_quest(language, quest, role or "admin"))
    await callback.answer()


async def _notify_answer_reviewers(
    message: Message, db: Database, quest: dict, answer_id: int, answer_text: str
) -> None:
    owner_id = int(quest["owner_id"])
    recipients = {owner_id}
    recipients.update(int(item["telegram_id"]) for item in await db.list_admins() if item["role"] == "superadmin")
    user = await db.get_user(message.from_user.id)
    sender_name = display_name(
        user.get("full_name") if user else None,
        user.get("username") if user else None,
        message.from_user.id,
    )
    answer_info = await db.get_pending_answer(answer_id) or {}
    for admin_id in recipients:
        language = await db.get_language(admin_id)
        text = tr(
            language,
            "pending_admin_notice",
            user=sender_name,
            title=quest["title"],
            stage=answer_info.get("stage_order", "?"),
            answer=answer_text,
        )
        try:
            await message.bot.send_message(admin_id, text, reply_markup=review_keyboard(language, answer_id))
        except TelegramAPIError:
            continue


async def _process_answer(message: Message, quest_id: int, db: Database, bot: Bot) -> None:
    if not message.from_user or not message.text:
        return
    language = await db.get_language(message.from_user.id)
    answer = message.text.strip()
    if not answer or len(answer) > 1000:
        await message.answer(tr(language, "invalid_text"))
        return
    result = await db.submit_answer(quest_id, message.from_user.id, answer, utc_now())
    code = result["code"]
    if code in {"not_joined", "closed"}:
        await message.answer(tr(language, "no_active_question"))
        return
    if code == "blocked":
        participant = await db.participant(quest_id, message.from_user.id)
        suffix = tr(language, "reason_line", reason=participant.get("ban_reason")) if participant and participant.get("ban_reason") else ""
        await message.answer(tr(language, "blocked_notice", reason=suffix))
        return
    if code in {"no_open_stage", "attempt_limit"}:
        await message.answer(tr(language, "no_active_question"))
        return
    if code == "overall_timeout":
        await message.answer(tr(language, "quest_ended"))
        return
    if code == "timeout":
        await message.answer(tr(language, "stage_timeout"))
        await db.maybe_complete_quest(quest_id)
        return
    if code == "pending":
        await message.answer(tr(language, "answer_pending"))
        quest = await db.get_quest(quest_id)
        if quest:
            await _notify_answer_reviewers(message, db, quest, int(result["answer_id"]), answer)
        return
    if code == "wrong":
        if result.get("exhausted"):
            await message.answer(tr(language, "attempts_exhausted"))
            await db.maybe_complete_quest(quest_id)
        else:
            await message.answer(tr(language, "wrong_answer", remaining=result["remaining"]))
        return
    if code != "correct":
        await message.answer(tr(language, "error_generic"))
        return
    if result.get("final"):
        await message.answer(tr(language, "correct_done"))
        await db.maybe_complete_quest(quest_id)
        return
    if result.get("next_stage_order"):
        await message.answer(tr(language, "correct_next"))
        quest = await db.get_quest(quest_id)
        stage = await db.get_stage(quest_id, int(result["next_stage_order"]))
        if quest and stage:
            me = await bot.get_me()
            await send_stage_to_user(bot, db, quest, stage, message.from_user.id, me.username or "")
        return
    await message.answer(tr(language, "correct_wait"))


@router.callback_query(F.data.startswith("answer:select:"))
async def select_answer_quest(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    stage = await db.current_open_stage(quest_id, callback.from_user.id)
    language = await db.get_language(callback.from_user.id)
    if not stage or stage.get("session_status") != "open":
        await callback.answer(tr(language, "no_active_question"), show_alert=True)
        return
    await state.update_data(answer_quest_id=quest_id)
    await state.set_state(AnswerFlow.answer)
    if callback.message:
        await callback.message.answer(tr(language, "send_answer"))
    await callback.answer()


@router.message(AnswerFlow.answer)
async def selected_quest_answer(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    if not message.text or message.text.startswith("/"):
        return
    data = await state.get_data()
    quest_id = int(data["answer_quest_id"])
    await state.clear()
    await _process_answer(message, quest_id, db, bot)


@router.message(F.chat.type == "private", F.text)
async def participant_answer(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    if not message.from_user or not message.text or message.text.startswith("/"):
        return
    stages = await db.open_stages_for_user(message.from_user.id)
    language = await db.get_language(message.from_user.id)
    if not stages:
        if await db.user_has_live_quest(message.from_user.id):
            await message.answer(tr(language, "no_active_question"))
        return
    if len(stages) == 1:
        await _process_answer(message, int(stages[0]["quest_id"]), db, bot)
        return
    await state.clear()
    await message.answer(tr(language, "choose_answer_quest"), reply_markup=answer_quest_selector(language, stages))
