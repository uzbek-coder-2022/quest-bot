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
    admin_home_keyboard,
    admin_quest_filters,
    answer_quest_selector,
    browse_filters,
    button,
    confirm_cover_removal_keyboard,
    edit_done_keyboard,
    edit_prompt_keyboard,
    edit_quest_cover_keyboard,
    edit_quest_details_keyboard,
    edit_stage_actions_keyboard,
    editable_stages_keyboard,
    home_keyboard,
    join_confirmation_keyboard,
    manage_quest,
    participating_quests_keyboard,
    quest_detail,
    ratings_overview_keyboard,
    review_keyboard,
)
from ..localization import tr
from ..periods import current_period_bounds
from ..presentation import (
    activity_message,
    copy_quest_cover,
    information_message,
    quest_preview,
)
from ..rich_text import bold, heading, paragraph, photo_block, quote, rich_message
from ..services import (
    archive_question_message,
    delete_archived_message,
    get_chat_invite_for_participant,
    send_current_stage_after_join,
    send_stage_to_user,
)
from ..states import AnswerFlow, EditQuestDetails, EditStage
from ..utils import (
    can_manage_quest,
    display_name,
    ensure_private_callback,
    safe_edit,
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


async def _show_public_page(
    callback: CallbackQuery, db: Database, status: str, page: int
) -> None:
    language = await db.get_language(callback.from_user.id)
    page_size = int(await db.settings_get("page_size", "10"))
    selected_status = None if status == "all" else status
    items = await db.list_public_quests(
        selected_status, max(0, page) * page_size, page_size
    )
    title = tr(language, "quests_title")
    if status != "all":
        title += f" · {tr(language, f'filter_{status}')}"
    text = information_message(title, None if items else tr(language, "empty_quests"))
    if callback.message:
        await safe_edit(
            callback,
            text,
            reply_markup=browse_filters(
                language, status, max(0, page), items, page_size
            ),
        )


async def _show_manage_page(
    callback: CallbackQuery, db: Database, status: str, page: int
) -> None:
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
    body = information_message(title, None if items else tr(language, "empty_quests"))
    if callback.message:
        await safe_edit(
            callback,
            body,
            reply_markup=admin_quest_filters(
                language, status, max(0, page), items, page_size
            ),
        )


@router.message(Command("quests"))
async def quests_command(message: Message, db: Database) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    page_size = int(await db.settings_get("page_size", "10"))
    items = await db.list_public_quests(None, 0, page_size)
    title = tr(language, "quests_title")
    await message.answer_rich(
        information_message(title, None if items else tr(language, "empty_quests")),
        reply_markup=browse_filters(language, "all", 0, items, page_size),
    )


@router.message(Command("myquests"))
async def my_quests_command(message: Message, db: Database) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    if message.chat.type != "private":
        await message.answer(tr(language, "open_private_chat"))
        return
    page_size = int(await db.settings_get("page_size", "20"))
    items = await db.list_user_quests(message.from_user.id, 0, page_size)
    text = information_message(
        tr(language, "my_participating_quests_title"),
        None if items else tr(language, "empty_quests"),
    )
    await message.answer_rich(
        text,
        reply_markup=participating_quests_keyboard(
            language, items, 0, page_size, "all"
        ),
    )


@router.callback_query(F.data.startswith("quest:mylist:"))
async def my_quests_callback(callback: CallbackQuery, db: Database) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        parts = callback.data.split(":")
        if len(parts) == 3:
            visibility = "all"
            page = max(0, int(parts[2]))
        elif len(parts) == 4:
            visibility = parts[2]
            page = max(0, int(parts[3]))
        else:
            raise ValueError("Invalid participating-quest list callback")
        if visibility not in {"all", "public", "private"}:
            raise ValueError("Invalid participating-quest visibility")
    except (ValueError, AttributeError):
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    page_size = int(await db.settings_get("page_size", "20"))
    items = await db.list_user_quests(
        callback.from_user.id,
        page * page_size,
        page_size,
        visibility=None if visibility == "all" else visibility,
    )
    title = tr(language, "my_participating_quests_title")
    if visibility != "all":
        title += f" · {tr(language, f'visibility_{visibility}')}"
    text = information_message(
        title,
        None if items else tr(language, "empty_quests"),
    )
    if callback.message:
        await safe_edit(
            callback,
            text,
            reply_markup=participating_quests_keyboard(
                language, items, page, page_size, visibility
            ),
        )
    await callback.answer()


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
async def view_quest_callback(callback: CallbackQuery, db: Database, bot: Bot) -> None:
    my_quests_visibility = "all"
    my_quests_page = 0
    try:
        parts = callback.data.split(":")
        if len(parts) == 6 and parts[2] == "my":
            from_my_quests = True
            my_quests_visibility = parts[3]
            my_quests_page = max(0, int(parts[4]))
            quest_id = int(parts[5])
            if my_quests_visibility not in {"all", "public", "private"}:
                raise ValueError("Invalid participating-quest visibility")
        elif len(parts) == 4 and parts[2] == "my":
            from_my_quests = True
            quest_id = int(parts[3])
        else:
            from_my_quests = False
            quest_id = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer()
        return
    quest = await db.get_quest(quest_id)
    language = await db.get_language(callback.from_user.id)
    if not quest:
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    if quest["visibility"] == "private" and not await ensure_private_callback(
        callback, db
    ):
        return
    participant = await db.participant(quest_id, callback.from_user.id)
    manager = await can_manage_quest(db, callback.from_user.id, quest)
    if (
        quest["visibility"] == "private"
        and not (participant and participant["status"] != "blocked")
        and not manager
    ):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return

    participants = await db.participant_count(quest_id)
    if callback.message:
        if not quest.get("cover_file_id"):
            await copy_quest_cover(bot, quest, callback.message.chat.id)
        await safe_edit(
            callback,
            quest_preview(quest, language, participants),
            reply_markup=quest_detail(
                language,
                quest,
                joined=bool(participant and participant["status"] != "blocked"),
                from_my_quests=from_my_quests,
                my_quests_visibility=my_quests_visibility,
                my_quests_page=my_quests_page,
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("quest:join:"))
async def request_quest_join(callback: CallbackQuery, db: Database) -> None:
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
    if quest.get("paused_at"):
        await callback.answer(tr(language, "quest_paused_notice"), show_alert=True)
        return
    if quest["status"] not in {"scheduled", "active"}:
        await callback.answer(tr(language, "join_closed"), show_alert=True)
        return
    participant = await db.participant(quest_id, callback.from_user.id)
    if participant:
        if participant["status"] == "blocked":
            suffix = (
                tr(language, "reason_line", reason=participant.get("ban_reason"))
                if participant.get("ban_reason")
                else ""
            )
            await callback.answer(
                tr(language, "blocked_notice", reason=suffix), show_alert=True
            )
        else:
            await callback.answer(tr(language, "already_joined"), show_alert=True)
        return
    participants = await db.participant_count(quest_id)
    if callback.message:
        await safe_edit(
            callback,
            quest_preview(quest, language, participants),
            reply_markup=join_confirmation_keyboard(language, quest_id),
        )
    await callback.answer(tr(language, "join_confirmation_prompt"))


@router.callback_query(F.data.startswith("quest:joinconfirm:"))
async def confirm_quest_join(callback: CallbackQuery, db: Database, bot: Bot) -> None:
    if not await ensure_private_callback(callback, db):
        return
    parts = callback.data.split(":", 3)
    if len(parts) < 3:
        await callback.answer()
        return
    try:
        quest_id = int(parts[2])
    except ValueError:
        await callback.answer()
        return
    token = parts[3] if len(parts) == 4 else None
    language = await db.get_language(callback.from_user.id)
    joined = await db.join_quest(quest_id, callback.from_user.id, token, utc_now())
    code = joined["code"]
    if code == "globally_banned":
        reason = joined.get("reason")
        suffix = tr(language, "reason_line", reason=reason) if reason else ""
        await callback.answer(
            tr(language, "global_blocked", reason=suffix), show_alert=True
        )
        return
    if code == "already_joined":
        await callback.answer(tr(language, "already_joined"), show_alert=True)
        return
    if code == "blocked":
        participant = await db.participant(quest_id, callback.from_user.id)
        suffix = (
            tr(language, "reason_line", reason=participant.get("ban_reason"))
            if participant and participant.get("ban_reason")
            else ""
        )
        await callback.answer(
            tr(language, "blocked_notice", reason=suffix), show_alert=True
        )
        return
    if code == "paused":
        await callback.answer(tr(language, "quest_paused_notice"), show_alert=True)
        return
    if code == "closed":
        await callback.answer(tr(language, "join_closed"), show_alert=True)
        return
    if code == "invalid_token":
        await callback.answer(tr(language, "private_link_invalid"), show_alert=True)
        return
    if code != "joined":
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return

    quest = await db.get_quest(quest_id)
    if not quest:
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    await db.log_action(callback.from_user.id, "participant.joined", "quest", quest_id)
    if callback.message:
        await callback.message.answer(tr(language, "join_success"))
        await safe_edit(
            callback,
            quest_preview(quest, language, await db.participant_count(quest_id)),
            reply_markup=quest_detail(language, quest, joined=True),
        )
    if quest.get("chat_id"):
        if quest["status"] == "active":
            invite_link = await get_chat_invite_for_participant(
                bot, db, quest, callback.from_user.id
            )
            await bot.send_message(
                callback.from_user.id,
                tr(language, "invite_link_ready", link=invite_link)
                if invite_link
                else tr(language, "invite_unavailable"),
            )
        else:
            await bot.send_message(
                callback.from_user.id, tr(language, "invite_at_start")
            )
    if quest["status"] == "active":
        me = await bot.get_me()
        await send_current_stage_after_join(
            bot, db, quest, callback.from_user.id, me.username or ""
        )
    await callback.answer()


@router.callback_query(F.data.startswith("quest:joincancel:"))
async def cancel_quest_join(callback: CallbackQuery, db: Database) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    quest = await db.get_quest(quest_id)
    participant = (
        await db.participant(quest_id, callback.from_user.id) if quest else None
    )
    if quest and quest["visibility"] == "public":
        if callback.message:
            await safe_edit(
                callback,
                quest_preview(quest, language, await db.participant_count(quest_id)),
                reply_markup=quest_detail(
                    language,
                    quest,
                    joined=bool(participant and participant["status"] != "blocked"),
                ),
            )
    elif callback.message:
        await safe_edit(
            callback,
            tr(language, "join_cancelled"),
            reply_markup=home_keyboard(language),
        )
    await callback.answer(tr(language, "join_cancelled"))


@router.callback_query(F.data.startswith("quest:chatinvite:"))
async def participant_chat_invite(
    callback: CallbackQuery, db: Database, bot: Bot
) -> None:
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
    if (
        not quest
        or quest["status"] != "active"
        or not quest.get("chat_id")
        or not participant
        or participant["status"] in {"blocked", "failed"}
    ):
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
    if quest["visibility"] == "private" and not await ensure_private_callback(
        callback, db
    ):
        return
    participant = await db.participant(quest_id, callback.from_user.id)
    if (
        quest["visibility"] == "private"
        and not (participant and participant["status"] != "blocked")
        and not await can_manage_quest(db, callback.from_user.id, quest)
    ):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    rows = await db.leaderboard(quest_id)
    title = tr(language, "leaderboard_title", title=quest["title"])
    rendered = []
    for rank, item in enumerate(rows[:30], start=1):
        name = display_name(
            item.get("full_name"), item.get("username"), item.get("user_id")
        )
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
    text = activity_message(
        title, rendered, None if rendered else tr(language, "leaderboard_empty")
    )
    role = await db.get_role(callback.from_user.id)
    markup = (
        admin_home_keyboard(language)
        if role in {"admin", "superadmin"}
        else home_keyboard(language)
    )
    if callback.message:
        await safe_edit(callback, text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "ratings:overview")
async def ratings_overview(callback: CallbackQuery, db: Database) -> None:
    language = await db.get_language(callback.from_user.id)
    role = await db.get_role(callback.from_user.id)
    if callback.message:
        await safe_edit(
            callback,
            information_message(tr(language, "ratings_overview_title")),
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
    title = tr(
        language,
        "aggregate_leaderboard_title",
        period=tr(language, f"period_{period}"),
    )
    rendered = []
    for rank, item in enumerate(items, start=1):
        name = display_name(
            item.get("full_name"), item.get("username"), item.get("user_id")
        )
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
    text = activity_message(
        title,
        rendered,
        None if rendered else tr(language, "aggregate_leaderboard_empty"),
    )
    role = await db.get_role(callback.from_user.id)
    return_home = (
        button(tr(language, "btn_admin_home"), "admin:home")
        if role in {"admin", "superadmin"}
        else button(tr(language, "btn_home"), "menu:home")
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [button(tr(language, "btn_back"), "ratings:overview")],
            [return_home],
        ]
    )
    if callback.message:
        await safe_edit(callback, text, reply_markup=markup)
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
        quests = await db.list_manageable_quests(
            callback.from_user.id, role == "superadmin", None, page * size, size
        )
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
    role = await db.get_role(callback.from_user.id)
    if role in {"admin", "superadmin"}:
        rows.append([button(tr(language, "btn_admin_home"), "admin:home")])
    else:
        rows.append([button(tr(language, "btn_home"), "menu:home")])
    title_key = (
        "btn_managed_ratings" if scope == "managed" else "btn_public_quest_ratings"
    )
    text = information_message(
        tr(language, title_key),
        None if quests else tr(language, "empty_quests"),
    )
    if callback.message:
        await safe_edit(
            callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
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
        await safe_edit(
            callback,
            quest_preview(quest, language, await db.participant_count(quest_id)),
            reply_markup=manage_quest(language, quest, role or "admin"),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:editdetails:"))
async def edit_quest_details_menu(callback: CallbackQuery, db: Database) -> None:
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
    if quest["status"] == "archived":
        await callback.answer(tr(language, "quest_metadata_archived"), show_alert=True)
        return
    if callback.message:
        await safe_edit(
            callback,
            rich_message(
                heading(f"🛠 {tr(language, 'edit_quest_details_title')}", size=1),
                paragraph(bold(f"🧭 {quest['title']}")),
                quote(f"💡 {tr(language, 'edit_quest_details_hint')}"),
            ),
            reply_markup=edit_quest_details_keyboard(language, quest_id),
        )
    await callback.answer()


async def _clear_fsm_and_archived_drafts(state: FSMContext, bot: Bot) -> None:
    """Clear an existing flow without orphaning its staged private archive messages."""
    data = await state.get_data()
    stages = data.get("stages") or []
    stage_draft = data.get("stage_draft") or {}
    for stage in [*stages, stage_draft]:
        await delete_archived_message(
            bot, stage.get("source_chat_id"), stage.get("source_message_id")
        )
    await delete_archived_message(
        bot, data.get("cover_chat_id"), data.get("cover_message_id")
    )
    await state.clear()


async def _begin_metadata_edit(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    field: str,
) -> None:
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
    if quest["status"] == "archived":
        await callback.answer(tr(language, "quest_metadata_archived"), show_alert=True)
        return

    await _clear_fsm_and_archived_drafts(state, callback.message.bot)
    await state.update_data(edit_quest_id=quest_id, edit_context="quest_metadata")
    if field == "title":
        await state.set_state(EditQuestDetails.title)
        prompt = rich_message(
            heading(f"✏️ {tr(language, 'ask_quest_title')}", size=1),
            paragraph(bold(f"📌 {tr(language, 'quest_label_title')}")),
            quote(str(quest["title"])),
        )
    else:
        await state.set_state(EditQuestDetails.description)
        prompt = rich_message(
            heading(f"📝 {tr(language, 'ask_quest_description')}", size=1),
            paragraph(bold(f"📄 {tr(language, 'quest_label_description')}")),
            quote(str(quest.get("description") or "—")),
        )
    if callback.message:
        await callback.message.answer_rich(
            prompt, reply_markup=edit_prompt_keyboard(language)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:edit:title:"))
async def begin_quest_title_edit(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    await _begin_metadata_edit(callback, state, db, "title")


@router.callback_query(F.data.startswith("manage:edit:description:"))
async def begin_quest_description_edit(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    await _begin_metadata_edit(callback, state, db, "description")


@router.callback_query(F.data.regexp(r"^manage:edit:cover:\d+$"))
async def quest_cover_editor(callback: CallbackQuery, db: Database, bot: Bot) -> None:
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
    if quest["status"] == "archived":
        await callback.answer(tr(language, "quest_metadata_archived"), show_alert=True)
        return
    has_cover = (
        quest.get("cover_chat_id") is not None
        and quest.get("cover_message_id") is not None
    )
    body = (
        tr(language, "edit_quest_cover_has_photo")
        if has_cover
        else tr(language, "edit_quest_cover_missing")
    )
    cover_file_id = quest.get("cover_file_id")
    has_inline_cover = isinstance(cover_file_id, str) and bool(cover_file_id.strip())
    blocks = [
        heading(f"🖼 {tr(language, 'edit_quest_cover_title')}", size=1),
        paragraph(bold(f"🧭 {quest['title']}")),
    ]
    if has_inline_cover:
        blocks.append(photo_block(cover_file_id))
    blocks.append(quote(body))
    if callback.message:
        edited = await safe_edit(
            callback,
            rich_message(*blocks),
            reply_markup=edit_quest_cover_keyboard(language, quest_id, has_cover),
        )
        if has_cover and not has_inline_cover and edited:
            await copy_quest_cover(bot, quest, callback.message.chat.id)
    await callback.answer()


@router.callback_query(F.data.startswith("manage:edit:cover:photo:"))
async def begin_quest_cover_photo_edit(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
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
    if quest["status"] == "archived":
        await callback.answer(tr(language, "quest_metadata_archived"), show_alert=True)
        return
    await _clear_fsm_and_archived_drafts(state, callback.message.bot)
    await state.update_data(edit_quest_id=quest_id, edit_context="quest_metadata")
    await state.set_state(EditQuestDetails.cover_photo)
    if callback.message:
        await callback.message.answer_rich(
            rich_message(
                heading(f"📷 {tr(language, 'ask_quest_cover')}", size=1),
                paragraph(bold(f"🧭 {quest['title']}")),
            ),
            reply_markup=edit_prompt_keyboard(language),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:edit:cover:remove:"))
async def confirm_quest_cover_removal_prompt(
    callback: CallbackQuery, db: Database
) -> None:
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
    if quest["status"] == "archived":
        await callback.answer(tr(language, "quest_metadata_archived"), show_alert=True)
        return
    if quest.get("cover_chat_id") is None or quest.get("cover_message_id") is None:
        await callback.answer(tr(language, "edit_quest_cover_missing"), show_alert=True)
        return
    if callback.message:
        await safe_edit(
            callback,
            rich_message(
                heading(f"⚠️ {tr(language, 'confirm_remove_cover')}", size=1),
                quote(str(quest["title"])),
            ),
            reply_markup=confirm_cover_removal_keyboard(language, quest_id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:edit:cover:remove-confirm:"))
async def remove_quest_cover(callback: CallbackQuery, db: Database) -> None:
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
    if quest["status"] == "archived":
        await callback.answer(tr(language, "quest_metadata_archived"), show_alert=True)
        return
    if quest.get("cover_chat_id") is None or quest.get("cover_message_id") is None:
        await callback.answer(tr(language, "edit_quest_cover_missing"), show_alert=True)
        return
    previous = await db.update_quest_metadata(
        quest_id,
        {
            "cover_chat_id": None,
            "cover_message_id": None,
            "cover_file_id": None,
        },
        callback.from_user.id,
        utc_now(),
    )
    if previous is None:
        await callback.answer(tr(language, "quest_metadata_archived"), show_alert=True)
        return
    await delete_archived_message(
        callback.message.bot,
        previous.get("cover_chat_id"),
        previous.get("cover_message_id"),
    )
    updated = await db.get_quest(quest_id)
    if callback.message and updated:
        await safe_edit(
            callback,
            rich_message(
                heading(f"✅ {tr(language, 'quest_cover_removed')}", size=1),
                paragraph(bold(f"🧭 {updated['title']}")),
            ),
            reply_markup=edit_quest_cover_keyboard(language, quest_id, False),
        )
    await callback.answer()


async def _metadata_message_quest(
    message: Message, state: FSMContext, db: Database
) -> tuple[int, dict] | None:
    data = await state.get_data()
    quest_id = data.get("edit_quest_id")
    if not message.from_user or quest_id is None:
        await state.clear()
        return None
    quest = await db.get_quest(int(quest_id))
    language = await db.get_language(message.from_user.id)
    if not quest or not await can_manage_quest(db, message.from_user.id, quest):
        await state.clear()
        await message.answer(tr(language, "quest_not_found"))
        return None
    if quest["status"] == "archived":
        await state.clear()
        await message.answer(tr(language, "quest_metadata_archived"))
        return None
    return int(quest_id), quest


@router.message(EditQuestDetails.title)
async def quest_title_received(
    message: Message, state: FSMContext, db: Database
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    access = await _metadata_message_quest(message, state, db)
    if not access:
        return
    title = (message.text or "").strip()
    if not 1 <= len(title) <= 100:
        await message.answer(
            f"⚠️ {tr(language, 'invalid_quest_title')}",
            reply_markup=edit_prompt_keyboard(language),
        )
        return
    quest_id, _ = access
    previous = await db.update_quest_metadata(
        quest_id, {"title": title}, message.from_user.id, utc_now()
    )
    if previous is None:
        await state.clear()
        await message.answer(tr(language, "quest_metadata_archived"))
        return
    await state.clear()
    await message.answer_rich(
        rich_message(
            heading(f"✅ {tr(language, 'quest_title_updated')}", size=1),
            paragraph(bold(f"📌 {tr(language, 'quest_label_title')}")),
            quote(title),
        ),
        reply_markup=edit_quest_details_keyboard(language, quest_id),
    )


@router.message(EditQuestDetails.description)
async def quest_description_received(
    message: Message, state: FSMContext, db: Database
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    access = await _metadata_message_quest(message, state, db)
    if not access:
        return
    description = (message.text or "").strip()
    if not 1 <= len(description) <= 1000:
        await message.answer(
            f"⚠️ {tr(language, 'invalid_quest_description')}",
            reply_markup=edit_prompt_keyboard(language),
        )
        return
    quest_id, _ = access
    previous = await db.update_quest_metadata(
        quest_id, {"description": description}, message.from_user.id, utc_now()
    )
    if previous is None:
        await state.clear()
        await message.answer(tr(language, "quest_metadata_archived"))
        return
    await state.clear()
    await message.answer_rich(
        rich_message(
            heading(f"✅ {tr(language, 'quest_description_updated')}", size=1),
            paragraph(bold(f"📄 {tr(language, 'quest_label_description')}")),
            quote(description),
        ),
        reply_markup=edit_quest_details_keyboard(language, quest_id),
    )


@router.message(EditQuestDetails.cover_photo, F.photo)
async def quest_cover_photo_received(
    message: Message, state: FSMContext, db: Database, settings: Settings
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    access = await _metadata_message_quest(message, state, db)
    if not access:
        return
    quest_id, _ = access
    try:
        archive_chat_id, archive_message_id = await archive_question_message(
            message.bot, message, settings.question_archive_channel_id
        )
    except TelegramAPIError:
        logger.exception(
            "Could not archive an edited quest cover from admin %s",
            message.from_user.id,
        )
        await message.answer(
            f"⚠️ {tr(language, 'question_archive_failed')}",
            reply_markup=edit_prompt_keyboard(language),
        )
        return
    try:
        previous = await db.update_quest_metadata(
            quest_id,
            {
                "cover_chat_id": archive_chat_id,
                "cover_message_id": archive_message_id,
                "cover_file_id": message.photo[-1].file_id,
            },
            message.from_user.id,
            utc_now(),
        )
    except Exception:
        await delete_archived_message(message.bot, archive_chat_id, archive_message_id)
        raise
    if previous is None:
        await delete_archived_message(message.bot, archive_chat_id, archive_message_id)
        await state.clear()
        await message.answer(tr(language, "quest_metadata_archived"))
        return
    await delete_archived_message(
        message.bot, previous.get("cover_chat_id"), previous.get("cover_message_id")
    )
    await state.clear()
    updated = await db.get_quest(quest_id)
    await message.answer_rich(
        rich_message(
            heading(f"✅ {tr(language, 'quest_cover_updated')}", size=1),
            paragraph(bold(f"🧭 {(updated or previous)['title']}")),
        ),
        reply_markup=edit_quest_details_keyboard(language, quest_id),
    )


@router.message(EditQuestDetails.cover_photo)
async def invalid_quest_cover_received(
    message: Message, state: FSMContext, db: Database
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    if not await _metadata_message_quest(message, state, db):
        return
    await message.answer(
        f"⚠️ {tr(language, 'invalid_quest_cover')}",
        reply_markup=edit_prompt_keyboard(language),
    )


@router.callback_query(F.data.startswith("manage:pause:"))
async def pause_quest_callback(callback: CallbackQuery, db: Database) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    quest = await db.get_quest(quest_id)
    language = await db.get_language(callback.from_user.id)
    if not quest or not await can_manage_quest(db, callback.from_user.id, quest):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    if not await db.pause_quest(quest_id, callback.from_user.id, utc_now()):
        await callback.answer(tr(language, "error_generic"), show_alert=True)
        return
    updated = await db.get_quest(quest_id)
    role = await db.get_role(callback.from_user.id)
    if callback.message and updated:
        await safe_edit(
            callback,
            quest_preview(updated, language, await db.participant_count(quest_id)),
            reply_markup=manage_quest(language, updated, role or "admin"),
        )
    await callback.answer(tr(language, "pause_success"), show_alert=True)


@router.callback_query(F.data.startswith("manage:resume:"))
async def resume_quest_callback(callback: CallbackQuery, db: Database) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    quest = await db.get_quest(quest_id)
    language = await db.get_language(callback.from_user.id)
    if not quest or not await can_manage_quest(db, callback.from_user.id, quest):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    shifted = await db.resume_quest(quest_id, callback.from_user.id, utc_now())
    if shifted is None:
        await callback.answer(tr(language, "error_generic"), show_alert=True)
        return
    updated = await db.get_quest(quest_id)
    role = await db.get_role(callback.from_user.id)
    if callback.message and updated:
        await safe_edit(
            callback,
            quest_preview(updated, language, await db.participant_count(quest_id)),
            reply_markup=manage_quest(language, updated, role or "admin"),
        )
    await callback.answer(tr(language, "resume_success"), show_alert=True)


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
        await safe_edit(
            callback,
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
        await safe_edit(
            callback,
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
        await callback.message.answer(
            prompt, reply_markup=edit_prompt_keyboard(language)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:editquestion:"))
async def begin_question_edit(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    await _begin_stage_edit(callback, state, db, "question")


@router.callback_query(F.data.startswith("manage:editanswer:"))
async def begin_answer_edit(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    await _begin_stage_edit(callback, state, db, "answer")


@router.callback_query(F.data == "manage:editcancel")
async def cancel_stage_edit(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    data = await state.get_data()
    await state.clear()
    language = await db.get_language(callback.from_user.id)
    quest_id = data.get("edit_quest_id")
    if callback.message:
        if data.get("edit_context") == "quest_metadata" and quest_id:
            quest = await db.get_quest(int(quest_id))
            markup = (
                edit_quest_details_keyboard(language, int(quest_id))
                if quest
                and quest["status"] != "archived"
                and await can_manage_quest(db, callback.from_user.id, quest)
                else admin_home_keyboard(language)
            )
        else:
            markup = edit_done_keyboard(language, int(quest_id)) if quest_id else None
        await callback.message.answer(
            f"↩️ {tr(language, 'edit_cancelled')}", reply_markup=markup
        )
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
        await message.answer(
            tr(await db.get_language(message.from_user.id), "admin_only")
        )
        return None
    quest_id, stage_order = int(quest_id), int(stage_order)
    if not await db.stage_is_editable(quest_id, stage_order, utc_now()):
        await state.clear()
        await message.answer(
            tr(await db.get_language(message.from_user.id), "stage_not_editable")
        )
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
        logger.exception(
            "Could not archive an edited question from admin %s", message.from_user.id
        )
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
async def replacement_answer_received(
    message: Message, state: FSMContext, db: Database
) -> None:
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
    recipients.update(
        int(item["telegram_id"])
        for item in await db.list_admins()
        if item["role"] == "superadmin"
    )
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
            await message.bot.send_message(
                admin_id, text, reply_markup=review_keyboard(language, answer_id)
            )
        except TelegramAPIError:
            continue


async def _process_answer(
    message: Message, quest_id: int, db: Database, bot: Bot
) -> None:
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
    if code == "paused":
        await message.answer(tr(language, "quest_paused_notice"))
        return
    if code == "blocked":
        participant = await db.participant(quest_id, message.from_user.id)
        suffix = (
            tr(language, "reason_line", reason=participant.get("ban_reason"))
            if participant and participant.get("ban_reason")
            else ""
        )
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
            await _notify_answer_reviewers(
                message, db, quest, int(result["answer_id"]), answer
            )
        return
    if code == "wrong":
        if result.get("exhausted"):
            await message.answer(tr(language, "attempts_exhausted"))
            await db.maybe_complete_quest(quest_id)
        else:
            await message.answer(
                tr(language, "wrong_answer", remaining=result["remaining"])
            )
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
            await send_stage_to_user(
                bot, db, quest, stage, message.from_user.id, me.username or ""
            )
        return
    await message.answer(tr(language, "correct_wait"))


@router.callback_query(F.data.startswith("answer:select:"))
async def select_answer_quest(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    stage = await db.current_open_stage(quest_id, callback.from_user.id)
    language = await db.get_language(callback.from_user.id)
    if stage and stage.get("paused_at"):
        await callback.answer(tr(language, "quest_paused_notice"), show_alert=True)
        return
    if not stage or stage.get("session_status") != "open":
        await callback.answer(tr(language, "no_active_question"), show_alert=True)
        return
    await state.update_data(answer_quest_id=quest_id)
    await state.set_state(AnswerFlow.answer)
    if callback.message:
        await callback.message.answer(tr(language, "send_answer"))
    await callback.answer()


@router.message(AnswerFlow.answer)
async def selected_quest_answer(
    message: Message, state: FSMContext, db: Database, bot: Bot
) -> None:
    if not message.text or message.text.startswith("/"):
        return
    data = await state.get_data()
    quest_id = int(data["answer_quest_id"])
    await state.clear()
    await _process_answer(message, quest_id, db, bot)


@router.message(F.chat.type == "private", F.text)
async def participant_answer(
    message: Message, state: FSMContext, db: Database, bot: Bot
) -> None:
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
    await message.answer(
        tr(language, "choose_answer_quest"),
        reply_markup=answer_quest_selector(language, stages),
    )
