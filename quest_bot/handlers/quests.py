"""Public discovery, quest joining, answer submission, and rankings."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from ..config import Settings
from ..database import Database, utc_now
from ..keyboards import (
    admin_quest_filters,
    answer_quest_selector,
    browse_filters,
    button,
    edit_done_keyboard,
    edit_prompt_keyboard,
    edit_stage_actions_keyboard,
    editable_stages_keyboard,
    home_keyboard,
    manage_quest,
    quest_detail,
    ratings_overview_keyboard,
    review_keyboard,
)
from ..localization import tr
from ..periods import current_period_bounds
from ..services import (
    archive_question_message,
    delete_archived_message,
    get_chat_invite_for_participant,
    send_current_stage_after_join,
    send_stage_to_user,
)
from ..states import AnswerFlow, EditStage
from ..utils import (
    can_manage_quest,
    display_name,
    ensure_private_callback,
    format_datetime,
)

router = Router(name="quests")
logger = logging.getLogger(__name__)


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


@router.callback_query(F.data == "ratings:overview")
async def ratings_overview(callback: CallbackQuery, db: Database) -> None:
    language = await db.get_language(callback.from_user.id)
    role = await db.get_role(callback.from_user.id)
    if callback.message:
        await callback.message.edit_text(
            tr(language, "ratings_overview_title"),
            reply_markup=ratings_overview_keyboard(language, role),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("ratings:period:"))
async def aggregate_leaderboard(callback: CallbackQuery, db: Database) -> None:
    period = callback.data.rsplit(":", 1)[1]
    if period not in {"week", "month", "year"}:
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    start_at, end_at = current_period_bounds(period)
    items = await db.aggregate_leaderboard(start_at, end_at, limit=30)
    text = tr(
        language,
        "aggregate_leaderboard_title",
        period=tr(language, f"period_{period}"),
    )
    if not items:
        text += f"\n\n{tr(language, 'aggregate_leaderboard_empty')}"
    else:
        rendered = []
        for rank, item in enumerate(items, start=1):
            name = display_name(item.get("full_name"), item.get("username"), item.get("user_id"))
            rendered.append(
                tr(
                    language,
                    "aggregate_leaderboard_row",
                    rank=rank,
                    name=name,
                    points=item["solved"],
                    completed=item["completed_quests"],
                )
            )
        text += "\n\n" + "\n".join(rendered)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [button(tr(language, "btn_back"), "ratings:overview")],
            [button(tr(language, "btn_home"), "menu:home")],
        ]
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("ratings:list:"))
async def leaderboard_quest_list(callback: CallbackQuery, db: Database) -> None:
    try:
        _, _, scope, page_text = callback.data.split(":", 3)
        page = int(page_text)
    except (ValueError, AttributeError):
        await callback.answer()
        return
    if scope not in {"all", "managed"} or page < 0:
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
    rows = [
        [button(tr(language, "btn_back"), "ratings:overview")],
        *[[button(item["title"][:50], f"rating:show:{item['id']}")] for item in quests],
    ]
    nav = []
    if page > 0:
        nav.append(button("◀", f"ratings:list:{scope}:{page - 1}"))
    if len(quests) == size:
        nav.append(button("▶", f"ratings:list:{scope}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([button(tr(language, "btn_home"), "menu:home")])
    title_key = "btn_managed_ratings" if scope == "managed" else "btn_public_quest_ratings"
    text = tr(language, title_key)
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


async def _editable_stage_list(db: Database, quest_id: int) -> list[dict]:
    now = utc_now()
    stages = await db.list_quest_stages(quest_id)
    editable = []
    for stage in stages:
        if await db.stage_is_editable(quest_id, int(stage["stage_order"]), now):
            editable.append(stage)
    return editable


@router.callback_query(F.data.startswith("manage:editquestions:"))
async def edit_questions_list(callback: CallbackQuery, db: Database) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    quest = await db.get_quest(quest_id)
    if not quest or not await can_manage_quest(db, callback.from_user.id, quest):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    stages = await _editable_stage_list(db, quest_id)
    text = f"{quest['title']}\n\n{tr(language, 'edit_questions_title')}"
    if not stages:
        text += f"\n\n{tr(language, 'no_editable_stages')}"
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=editable_stages_keyboard(language, quest_id, stages),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:editstage:"))
async def edit_stage_options(callback: CallbackQuery, db: Database) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        _, _, quest_id_text, stage_order_text = callback.data.split(":", 3)
        quest_id, stage_order = int(quest_id_text), int(stage_order_text)
    except (ValueError, AttributeError):
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    quest = await db.get_quest(quest_id)
    stage = await db.get_stage(quest_id, stage_order)
    if (
        not quest
        or not stage
        or not await can_manage_quest(db, callback.from_user.id, quest)
        or not await db.stage_is_editable(quest_id, stage_order, utc_now())
    ):
        await callback.answer(tr(language, "stage_not_editable"), show_alert=True)
        return
    if callback.message:
        await callback.message.edit_text(
            tr(language, "edit_stage_title", number=stage_order),
            reply_markup=edit_stage_actions_keyboard(
                language, quest_id, stage_order, stage["answer_mode"] == "auto"
            ),
        )
    await callback.answer()


async def _begin_stage_edit(
    callback: CallbackQuery, state: FSMContext, db: Database, edit_kind: str
) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        _, _, quest_id_text, stage_order_text = callback.data.split(":", 3)
        quest_id, stage_order = int(quest_id_text), int(stage_order_text)
    except (ValueError, AttributeError):
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    quest = await db.get_quest(quest_id)
    stage = await db.get_stage(quest_id, stage_order)
    if (
        not quest
        or not stage
        or not await can_manage_quest(db, callback.from_user.id, quest)
        or not await db.stage_is_editable(quest_id, stage_order, utc_now())
        or (edit_kind == "answer" and stage["answer_mode"] != "auto")
    ):
        await callback.answer(tr(language, "stage_not_editable"), show_alert=True)
        return
    await state.clear()
    await state.update_data(edit_quest_id=quest_id, edit_stage_order=stage_order)
    if edit_kind == "question":
        await state.set_state(EditStage.question)
        prompt = tr(language, "ask_replacement_question")
    else:
        await state.set_state(EditStage.answer)
        prompt = tr(language, "ask_replacement_answer")
    if callback.message:
        await callback.message.answer(prompt, reply_markup=edit_prompt_keyboard(language))
    await callback.answer()


@router.callback_query(F.data.startswith("manage:editquestion:"))
async def begin_question_edit(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await _begin_stage_edit(callback, state, db, "question")


@router.callback_query(F.data.startswith("manage:editanswer:"))
async def begin_answer_edit(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await _begin_stage_edit(callback, state, db, "answer")


@router.callback_query(F.data == "manage:editcancel")
async def cancel_stage_edit(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    await state.clear()
    language = await db.get_language(callback.from_user.id)
    quest_id = data.get("edit_quest_id")
    if callback.message:
        markup = edit_done_keyboard(language, int(quest_id)) if quest_id else None
        await callback.message.answer(tr(language, "edit_cancelled"), reply_markup=markup)
    await callback.answer()


async def _message_edit_access(
    message: Message, state: FSMContext, db: Database
) -> tuple[int, int] | None:
    data = await state.get_data()
    quest_id = data.get("edit_quest_id")
    stage_order = data.get("edit_stage_order")
    if not message.from_user or quest_id is None or stage_order is None:
        await state.clear()
        return None
    quest = await db.get_quest(int(quest_id))
    if not quest or not await can_manage_quest(db, message.from_user.id, quest):
        await state.clear()
        await message.answer(tr(await db.get_language(message.from_user.id), "admin_only"))
        return None
    quest_id, stage_order = int(quest_id), int(stage_order)
    if not await db.stage_is_editable(quest_id, stage_order, utc_now()):
        await state.clear()
        await message.answer(tr(await db.get_language(message.from_user.id), "stage_not_editable"))
        return None
    return quest_id, stage_order


def _replacement_question_text(message: Message) -> str | None:
    if message.text is not None:
        question = message.text.strip()
        return question if question and len(question) <= 4096 else None
    if message.photo or message.video:
        caption = (message.caption or "").strip()
        return caption if len(caption) <= 1024 else None
    return None


@router.message(EditStage.question)
async def replacement_question_received(
    message: Message, state: FSMContext, db: Database, settings: Settings
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    question = _replacement_question_text(message)
    if question is None:
        await message.answer(tr(language, "invalid_question"))
        return
    edit_context = await _message_edit_access(message, state, db)
    if not edit_context:
        return
    quest_id, stage_order = edit_context
    try:
        archive_chat_id, archive_message_id = await archive_question_message(
            message.bot, message, settings.question_archive_channel_id
        )
    except TelegramAPIError:
        logger.exception("Could not archive an edited question from admin %s", message.from_user.id)
        await message.answer(tr(language, "question_archive_failed"))
        return
    try:
        previous = await db.update_stage_question(
            quest_id,
            stage_order,
            question,
            archive_chat_id,
            archive_message_id,
            message.from_user.id,
            utc_now(),
        )
    except Exception:
        await delete_archived_message(message.bot, archive_chat_id, archive_message_id)
        raise
    if previous is None:
        await delete_archived_message(message.bot, archive_chat_id, archive_message_id)
        await state.clear()
        await message.answer(tr(language, "stage_not_editable"))
        return
    await delete_archived_message(
        message.bot,
        previous.get("source_chat_id"),
        previous.get("source_message_id"),
    )
    await state.clear()
    await message.answer(
        tr(language, "stage_question_updated"),
        reply_markup=edit_done_keyboard(language, quest_id),
    )


@router.message(EditStage.answer)
async def replacement_answer_received(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    answer = (message.text or "").strip()
    if not answer or len(answer) > 300:
        await message.answer(tr(language, "invalid_text"))
        return
    edit_context = await _message_edit_access(message, state, db)
    if not edit_context:
        return
    quest_id, stage_order = edit_context
    updated = await db.update_stage_answer(
        quest_id, stage_order, answer, message.from_user.id, utc_now()
    )
    if not updated:
        await state.clear()
        await message.answer(tr(language, "stage_not_editable"))
        return
    await state.clear()
    await message.answer(
        tr(language, "stage_answer_updated"),
        reply_markup=edit_done_keyboard(language, quest_id),
    )


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
