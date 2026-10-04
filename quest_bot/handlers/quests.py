"""Public discovery, quest joining, answer submission, and rankings."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from ..config import Settings
from ..database import Database, utc_now
from ..keyboards import (
    add_stage_answer_mode_keyboard,
    admin_home_keyboard,
    admin_quest_filters,
    browse_filters,
    button,
    confirm_cover_removal_keyboard,
    continue_keyboard,
    deleted_quest_keyboard,
    edit_done_keyboard,
    edit_prompt_keyboard,
    edit_quest_cover_keyboard,
    edit_quest_details_keyboard,
    edit_stage_actions_keyboard,
    editable_stages_keyboard,
    home_keyboard,
    join_confirmation_keyboard,
    manage_quest,
    open_quest_keyboard,
    participating_quests_keyboard,
    quest_detail,
    ratings_overview_keyboard,
    remove_stage_confirmation_keyboard,
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
    archive_telegram_message,
    delete_archived_message,
    get_chat_invite_for_participant,
    notify_next_stage_ready,
    notify_participant_stage_ready,
    notify_quest_end,
    question_media_details,
    send_stage_to_user,
)
from ..states import AddStage, AnswerFlow, EditQuestDetails, EditStage
from ..utils import (
    can_manage_quest,
    display_name,
    ensure_private_callback,
    format_datetime,
    parse_local_datetime,
    rank_label,
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
    if status == "deleted" and role != "superadmin":
        await callback.answer(tr(language, "superadmin_only"), show_alert=True)
        return
    page_size = int(await db.settings_get("page_size", "10"))
    selected_status = None if status in {"all", "deleted"} else status
    items = await db.list_manageable_quests(
        callback.from_user.id,
        role == "superadmin",
        selected_status,
        max(0, page) * page_size,
        page_size,
        deleted=status == "deleted",
    )
    title = tr(language, "my_quests_title")
    if status == "deleted":
        title += f" · {tr(language, 'filter_deleted')}"
    body = information_message(title, None if items else tr(language, "empty_quests"))
    if callback.message:
        await safe_edit(
            callback,
            body,
            reply_markup=admin_quest_filters(
                language, status, max(0, page), items, page_size, role=role
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
    if status not in {
        "all",
        "scheduled",
        "active",
        "completed",
        "archived",
        "deleted",
    }:
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

    can_continue = False
    continue_started = bool(participant and int(participant.get("current_stage") or 0) > 0)
    if participant and participant["status"] in {"joined", "active"}:
        deliverable = await db.next_deliverable_stage(
            quest_id, callback.from_user.id, utc_now()
        )
        can_continue = deliverable is not None

    participants = await db.participant_count(quest_id)
    if callback.message:
        has_inline_cover = bool(quest.get("cover_file_id"))
        has_legacy_cover = (
            not has_inline_cover
            and quest.get("cover_chat_id") is not None
            and quest.get("cover_message_id") is not None
        )
        preview = quest_preview(quest, language, participants)
        markup = quest_detail(
            language,
            quest,
            joined=bool(participant and participant["status"] != "blocked"),
            from_my_quests=from_my_quests,
            my_quests_visibility=my_quests_visibility,
            my_quests_page=my_quests_page,
            can_continue=can_continue,
            continue_started=continue_started,
        )
        if has_legacy_cover:
            try:
                await callback.message.delete()
            except TelegramAPIError as exc:
                logger.debug(
                    "Could not remove the previous quest menu before showing legacy cover %s: %s",
                    quest_id,
                    exc,
                )
            await copy_quest_cover(bot, quest, callback.message.chat.id)
            await callback.message.answer_rich(preview, reply_markup=markup)
        else:
            await safe_edit(callback, preview, reply_markup=markup)
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
        await notify_participant_stage_ready(bot, db, quest, callback.from_user.id)
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


def _leaderboard_origin(data: str) -> tuple[str, list[str]]:
    """Split a leaderboard callback into its origin name and extra parts."""
    parts = (data or "").split(":")
    if len(parts) <= 3:
        return "browse", []
    return parts[3], parts[4:]


def _quest_card_target(quest_id: int, extra: list[str]) -> str:
    """Quest-page callback that keeps the card's own Back destination."""
    if extra and extra[0] == "my":
        visibility = (
            extra[1]
            if len(extra) > 1 and extra[1] in {"all", "public", "private"}
            else "all"
        )
        try:
            page = max(0, int(extra[2]))
        except (IndexError, ValueError):
            page = 0
        return f"quest:view:my:{visibility}:{page}:{quest_id}"
    return f"quest:view:{quest_id}"


def _leaderboard_back_target(data: str, quest_id: int) -> str:
    """Translate a leaderboard callback origin into its return destination.

    A rating opened from a quest card returns to that card; a rating opened
    from the ratings list returns to the list it was opened from.
    """
    origin, extra = _leaderboard_origin(data)
    if origin == "card":
        return _quest_card_target(quest_id, extra)
    if origin == "my":
        visibility = (
            extra[0] if extra and extra[0] in {"all", "public", "private"} else "all"
        )
        try:
            page = max(0, int(extra[1]))
        except (IndexError, ValueError):
            page = 0
        return f"quest:mylist:{visibility}:{page}"
    if origin == "manage":
        return f"manage:quest:{quest_id}"
    if origin == "list":
        scope = (
            extra[0] if extra and extra[0] in {"all", "managed"} else "all"
        )
        try:
            page = max(0, int(extra[1]))
        except (IndexError, ValueError):
            page = 0
        return f"ratings:list:{scope}:{page}"
    if origin == "browse":
        return "browse:filter:all:0"
    return "menu:home"


def _leaderboard_open_target(data: str, quest_id: int) -> str:
    """Destination of the Open-quest button under a leaderboard."""
    origin, extra = _leaderboard_origin(data)
    if origin == "card":
        return _quest_card_target(quest_id, extra)
    if origin == "manage":
        return f"manage:quest:{quest_id}"
    return f"quest:view:{quest_id}"


@router.callback_query(F.data.startswith("rating:show:"))
async def show_leaderboard(callback: CallbackQuery, db: Database) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        await callback.answer()
        return
    try:
        quest_id = int(parts[2])
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
    leaderboard_rows = await db.leaderboard(quest_id)
    title = tr(language, "leaderboard_title", title=quest["title"])
    rendered = []
    for rank, item in enumerate(leaderboard_rows[:30], start=1):
        name = display_name(
            item.get("full_name"), item.get("username"), item.get("user_id")
        )
        completion = (
            tr(
                language,
                "leaderboard_completion",
                time=format_datetime(item["completed_at"], language),
            )
            if item.get("status") == "completed" and item.get("completed_at")
            else ""
        )
        rendered.append(
            tr(
                language,
                "leaderboard_row",
                rank=rank_label(rank),
                name=name,
                solved=item["solved"],
                status=_participant_status(language, item["status"]),
                completion=completion,
            )
        )
    text = activity_message(
        title, rendered, None if rendered else tr(language, "leaderboard_empty")
    )
    manager = await can_manage_quest(db, callback.from_user.id, quest)
    home_button = (
        button(tr(language, "btn_admin_home"), "admin:home")
        if manager
        else button(tr(language, "btn_home"), "menu:home")
    )
    rows = [
        [
            button(
                tr(language, "btn_open_quest"),
                _leaderboard_open_target(callback.data, quest_id),
            )
        ],
        [
            button(
                tr(language, "btn_back"),
                _leaderboard_back_target(callback.data, quest_id),
            )
        ],
        [home_button],
    ]
    if callback.message:
        await safe_edit(
            callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
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
                rank=rank_label(rank),
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
        *[
            [
                button(
                    item["title"][:50],
                    f"rating:show:{item['id']}:list:{scope}:{page}",
                )
            ]
            for item in quests
        ],
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
    if not quest:
        deleted_quest = await db.get_quest(quest_id, include_deleted=True)
        if deleted_quest and deleted_quest["deleted"]:
            if role != "superadmin":
                await callback.answer(tr(language, "quest_not_found"), show_alert=True)
                return
            if callback.message:
                await safe_edit(
                    callback,
                    information_message(
                        tr(language, "deleted_quest_view_title"),
                        tr(language, "deleted_quest_view_body")
                        + "\n\n"
                        + tr(language, "deleted_quest_purge_hint"),
                    ),
                    reply_markup=deleted_quest_keyboard(language, quest_id),
                )
            await callback.answer()
            return
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
    if field == "start_at" and quest["status"] != "scheduled":
        await callback.answer(tr(language, "quest_start_edit_closed"), show_alert=True)
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
    elif field == "start_at":
        await state.set_state(EditQuestDetails.start_at)
        prompt = rich_message(
            heading(f"🕒 {tr(language, 'ask_quest_start')}", size=1),
            quote(
                tr(
                    language,
                    "quest_start_edit_hint",
                    current=format_datetime(quest["start_at"], language),
                )
            ),
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


@router.callback_query(F.data.startswith("manage:edit:start:"))
async def begin_quest_start_edit(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    await _begin_metadata_edit(callback, state, db, "start_at")


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


@router.message(EditQuestDetails.start_at)
async def quest_start_received(
    message: Message, state: FSMContext, db: Database
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    access = await _metadata_message_quest(message, state, db)
    if not access:
        return
    start_at = parse_local_datetime(message.text or "")
    if not start_at or datetime.fromisoformat(start_at) <= datetime.now(timezone.utc):
        await message.answer(
            f"⚠️ {tr(language, 'invalid_quest_start')}",
            reply_markup=edit_prompt_keyboard(language),
        )
        return
    quest_id, _ = access
    previous = await db.update_quest_start_at(
        quest_id, start_at, message.from_user.id, utc_now()
    )
    if previous is None:
        await state.clear()
        await message.answer(tr(language, "quest_start_edit_closed"))
        return
    await state.clear()
    await message.answer_rich(
        rich_message(
            heading(f"✅ {tr(language, 'quest_start_updated')}", size=1),
            paragraph(bold(f"🕒 {tr(language, 'btn_edit_start')}")),
            quote(format_datetime(start_at, language)),
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


async def _stage_editor_keyboard(
    db: Database, language: str, quest_id: int, now: str
) -> tuple[list[dict], InlineKeyboardMarkup]:
    stages, removable_orders, allow_add = await db.stage_structure_options(
        quest_id, now
    )
    return stages, editable_stages_keyboard(
        language, quest_id, stages, removable_orders, allow_add
    )


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
    stages, markup = await _stage_editor_keyboard(db, language, quest_id, utc_now())
    text = f"{quest['title']}\n\n{tr(language, 'edit_questions_title')}"
    if not stages:
        text += f"\n\n{tr(language, 'no_editable_stages')}"
    if callback.message:
        await safe_edit(callback, text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^manage:addstage:\d+$"))
async def begin_stage_addition(
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
    stages = await db.list_quest_stages(quest_id)
    if len(stages) >= 30:
        await callback.answer(tr(language, "stage_limit_reached"), show_alert=True)
        return
    if not await db.can_add_stage(quest_id, utc_now()):
        await callback.answer(tr(language, "stage_not_editable"), show_alert=True)
        return
    await state.clear()
    await state.update_data(
        edit_quest_id=quest_id,
        edit_context="add_stage",
        stage_draft={},
    )
    await state.set_state(AddStage.question)
    if callback.message:
        await callback.message.answer(
            tr(language, "ask_question", number=len(stages) + 1),
            reply_markup=edit_prompt_keyboard(language),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:removestage:"))
async def confirm_stage_removal_prompt(
    callback: CallbackQuery, db: Database
) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        _, _, quest_text, order_text = callback.data.split(":", 3)
        quest_id, stage_order = int(quest_text), int(order_text)
    except (ValueError, AttributeError):
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    quest = await db.get_quest(quest_id)
    if (
        not quest
        or not await can_manage_quest(db, callback.from_user.id, quest)
        or not await db.can_remove_stage(quest_id, stage_order, utc_now())
    ):
        await callback.answer(tr(language, "stage_not_editable"), show_alert=True)
        return
    if callback.message:
        await safe_edit(
            callback,
            tr(language, "confirm_remove_stage", number=stage_order),
            reply_markup=remove_stage_confirmation_keyboard(
                language, quest_id, stage_order
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:removestageconfirm:"))
async def remove_stage_confirmed(callback: CallbackQuery, db: Database) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        _, _, quest_text, order_text = callback.data.split(":", 3)
        quest_id, stage_order = int(quest_text), int(order_text)
    except (ValueError, AttributeError):
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    quest = await db.get_quest(quest_id)
    if not quest or not await can_manage_quest(db, callback.from_user.id, quest):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    previous = await db.remove_stage(
        quest_id, stage_order, callback.from_user.id, utc_now()
    )
    if not previous:
        await callback.answer(tr(language, "stage_not_editable"), show_alert=True)
        return
    bot = callback.message.bot if callback.message else callback.bot
    await delete_archived_message(
        bot, previous.get("source_chat_id"), previous.get("source_message_id")
    )
    if await db.maybe_complete_quest(quest_id):
        user_ids = await db.all_participant_ids(quest_id, ("joined", "active"))
        await notify_quest_end(bot, db, quest_id, user_ids)
    stages = await db.list_quest_stages(quest_id)
    editable, markup = await _stage_editor_keyboard(
        db, language, quest_id, utc_now()
    )
    current_quest = await db.get_quest(quest_id)
    title = current_quest["title"] if current_quest else quest["title"]
    text = f"{title}\n\n{tr(language, 'edit_questions_title')}"
    if not editable:
        text += f"\n\n{tr(language, 'no_editable_stages')}"
    if callback.message:
        await safe_edit(callback, text, reply_markup=markup)
    await callback.answer(
        tr(language, "stage_removed", count=len(stages)), show_alert=True
    )


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


async def _stage_add_context(
    user_id: int, state: FSMContext, db: Database
) -> tuple[int, dict, list[dict], dict] | None:
    data = await state.get_data()
    try:
        quest_id = int(data["edit_quest_id"])
    except (KeyError, TypeError, ValueError):
        return None
    quest = await db.get_quest(quest_id)
    if not quest or not await can_manage_quest(db, user_id, quest):
        return None
    stages = await db.list_quest_stages(quest_id)
    if not await db.can_add_stage(quest_id, utc_now()):
        return None
    return quest_id, quest, stages, data


def _utc_datetime(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _added_stage_start_is_valid(
    quest: dict, stages: list[dict], starts_at: str, now: str
) -> bool:
    if not stages:
        return False
    new_start = _utc_datetime(starts_at)
    if quest["progression"] == "scheduled":
        if new_start <= _utc_datetime(str(stages[-1]["starts_at"])):
            return False
        if (
            quest["status"] == "active"
            and not quest.get("paused_at")
            and new_start <= _utc_datetime(now)
        ):
            return False
    elif new_start != _utc_datetime(str(quest["start_at"])):
        return False
    duration = int(quest.get("duration_seconds") or 0)
    if duration:
        deadline = _utc_datetime(str(quest["start_at"])) + timedelta(
            seconds=duration
        )
        if new_start > deadline:
            return False
    return True


async def _finish_stage_addition(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    starts_at: str,
) -> None:
    language = await db.get_language(message.from_user.id)
    now = utc_now()
    latest = await _stage_add_context(message.from_user.id, state, db)
    if not latest:
        await state.clear()
        await message.answer(tr(language, "stage_not_editable"))
        return
    quest_id, quest, stages, data = latest
    if not _added_stage_start_is_valid(quest, stages, starts_at, now):
        await message.answer(tr(language, "stage_schedule_invalid"))
        return
    draft = data.get("stage_draft", {})
    try:
        source_chat_id = int(draft["input_chat_id"])
        source_message_id = int(draft["input_message_id"])
        question = str(draft["question"])
        question_media_type = str(draft.get("question_media_type", "legacy"))
        question_file_id = draft.get("question_file_id")
        answer_mode = str(draft["answer_mode"])
        correct_answer = draft.get("correct_answer")
        max_attempts = int(draft["max_attempts"])
        time_limit_seconds = int(draft["time_limit_seconds"])
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await message.answer(tr(language, "error_generic"))
        return

    try:
        archive_chat_id, archive_message_id = await archive_telegram_message(
            message.bot,
            source_chat_id,
            source_message_id,
            settings.question_archive_channel_id,
        )
    except TelegramAPIError:
        logger.exception(
            "Could not archive an added stage question from admin %s",
            message.from_user.id,
        )
        await message.answer(tr(language, "question_archive_failed"))
        return

    try:
        new_order = await db.add_stage(
            quest_id,
            {
                "question": question,
                "question_media_type": question_media_type,
                "question_file_id": question_file_id,
                "answer_mode": answer_mode,
                "correct_answer": correct_answer,
                "max_attempts": max_attempts,
                "time_limit_seconds": time_limit_seconds,
                "starts_at": starts_at,
                "source_chat_id": archive_chat_id,
                "source_message_id": archive_message_id,
            },
            message.from_user.id,
            now,
        )
    except Exception:
        await delete_archived_message(message.bot, archive_chat_id, archive_message_id)
        raise
    if new_order is None:
        await delete_archived_message(message.bot, archive_chat_id, archive_message_id)
        await state.clear()
        await message.answer(tr(language, "stage_not_editable"))
        return

    await state.clear()
    total = len(await db.list_quest_stages(quest_id))
    await message.answer(
        tr(language, "stage_added", number=new_order, count=total),
        reply_markup=edit_done_keyboard(language, quest_id),
    )


@router.message(AddStage.question)
async def added_stage_question_received(
    message: Message, state: FSMContext, db: Database
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    context = await _stage_add_context(message.from_user.id, state, db)
    if not context:
        await state.clear()
        await message.answer(tr(language, "stage_not_editable"))
        return
    question = _replacement_question_text(message)
    if question is None:
        await message.answer(tr(language, "invalid_question"))
        return
    question_media_type, question_file_id = question_media_details(message)
    draft = {
        "question": question,
        "question_media_type": question_media_type,
        "question_file_id": question_file_id,
        "input_chat_id": message.chat.id,
        "input_message_id": message.message_id,
    }
    await state.update_data(stage_draft=draft)
    await state.set_state(AddStage.answer_mode)
    await message.answer(
        tr(language, "ask_answer_mode"),
        reply_markup=add_stage_answer_mode_keyboard(language),
    )


@router.callback_query(
    AddStage.answer_mode, F.data.startswith("manage:addstage:answer:")
)
async def added_stage_answer_mode_selected(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    if not await ensure_private_callback(callback, db):
        return
    mode = callback.data.rsplit(":", 1)[1]
    if mode not in {"auto", "manual"}:
        await callback.answer()
        return
    context = await _stage_add_context(callback.from_user.id, state, db)
    language = await db.get_language(callback.from_user.id)
    if not context:
        await state.clear()
        await callback.answer(tr(language, "stage_not_editable"), show_alert=True)
        return
    data = context[3]
    draft = dict(data.get("stage_draft", {}))
    draft["answer_mode"] = mode
    if mode == "manual":
        draft["correct_answer"] = None
        await state.update_data(stage_draft=draft)
        await state.set_state(AddStage.max_attempts)
        if callback.message:
            await callback.message.answer(
                tr(language, "ask_attempts"),
                reply_markup=edit_prompt_keyboard(language),
            )
    else:
        await state.update_data(stage_draft=draft)
        await state.set_state(AddStage.correct_answer)
        if callback.message:
            await callback.message.answer(
                tr(language, "ask_correct_answer"),
                reply_markup=edit_prompt_keyboard(language),
            )
    await callback.answer()


@router.message(AddStage.correct_answer)
async def added_stage_correct_answer_received(
    message: Message, state: FSMContext, db: Database
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    if not await _stage_add_context(message.from_user.id, state, db):
        await state.clear()
        await message.answer(tr(language, "stage_not_editable"))
        return
    answer = (message.text or "").strip()
    if not answer or len(answer) > 300:
        await message.answer(tr(language, "invalid_correct_answer"))
        return
    data = await state.get_data()
    draft = dict(data.get("stage_draft", {}))
    draft["correct_answer"] = answer
    await state.update_data(stage_draft=draft)
    await state.set_state(AddStage.max_attempts)
    await message.answer(
        tr(language, "ask_attempts"),
        reply_markup=edit_prompt_keyboard(language),
    )


@router.message(AddStage.max_attempts)
async def added_stage_attempts_received(
    message: Message, state: FSMContext, db: Database
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    if not await _stage_add_context(message.from_user.id, state, db):
        await state.clear()
        await message.answer(tr(language, "stage_not_editable"))
        return
    try:
        attempts = int((message.text or "").strip())
    except ValueError:
        await message.answer(tr(language, "invalid_number"))
        return
    if not 1 <= attempts <= 100:
        await message.answer(tr(language, "invalid_number"))
        return
    data = await state.get_data()
    draft = dict(data.get("stage_draft", {}))
    draft["max_attempts"] = attempts
    await state.update_data(stage_draft=draft)
    await state.set_state(AddStage.time_limit)
    await message.answer(
        tr(language, "ask_stage_time"),
        reply_markup=edit_prompt_keyboard(language),
    )


@router.message(AddStage.time_limit)
async def added_stage_time_limit_received(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    context = await _stage_add_context(message.from_user.id, state, db)
    if not context:
        await state.clear()
        await message.answer(tr(language, "stage_not_editable"))
        return
    try:
        minutes = int((message.text or "").strip())
    except ValueError:
        await message.answer(tr(language, "invalid_number"))
        return
    if not 0 <= minutes <= 525600:
        await message.answer(tr(language, "invalid_number"))
        return
    _, quest, stages, data = context
    draft = dict(data.get("stage_draft", {}))
    draft["time_limit_seconds"] = minutes * 60
    await state.update_data(stage_draft=draft)
    if quest["progression"] == "scheduled":
        await state.set_state(AddStage.start_at)
        await message.answer(
            tr(language, "ask_stage_start", number=len(stages) + 1),
            reply_markup=edit_prompt_keyboard(language),
        )
        return
    await _finish_stage_addition(
        message, state, db, settings, str(quest["start_at"])
    )


@router.message(AddStage.start_at)
async def added_stage_start_received(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    context = await _stage_add_context(message.from_user.id, state, db)
    if not context:
        await state.clear()
        await message.answer(tr(language, "stage_not_editable"))
        return
    starts_at = parse_local_datetime(message.text or "")
    if not starts_at:
        await message.answer(tr(language, "invalid_datetime"))
        return
    _, quest, stages, _ = context
    if not _added_stage_start_is_valid(quest, stages, starts_at, utc_now()):
        await message.answer(tr(language, "stage_schedule_invalid"))
        return
    await _finish_stage_addition(message, state, db, settings, starts_at)


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
    question_media_type, question_file_id = question_media_details(message)
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
            question_media_type=question_media_type,
            question_file_id=question_file_id,
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
    message: Message,
    quest_id: int,
    db: Database,
    bot: Bot,
    answer_text: str | None = None,
    actor_id: int | None = None,
) -> None:
    """Grade one participant answer.

    ``answer_text`` and ``actor_id`` let callers submit text that was collected
    earlier, for example when a support message turned out to be an answer.
    """
    user_id = actor_id or (message.from_user.id if message.from_user else None)
    if user_id is None:
        return
    language = await db.get_language(user_id)
    answer = (answer_text if answer_text is not None else (message.text or "")).strip()
    if not answer or len(answer) > 1000:
        await message.answer(tr(language, "invalid_text"))
        return
    result = await db.submit_answer(quest_id, user_id, answer, utc_now())
    code = result["code"]
    if code in {"not_joined", "closed"}:
        await message.answer(tr(language, "no_active_question"))
        return
    if code == "paused":
        await message.answer(tr(language, "quest_paused_notice"))
        return
    if code == "blocked":
        participant = await db.participant(quest_id, user_id)
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
        await message.answer(
            tr(language, "quest_ended"),
            reply_markup=open_quest_keyboard(language, quest_id),
        )
        return
    if code == "timeout":
        await message.answer(
            tr(language, "stage_timeout"),
            reply_markup=open_quest_keyboard(language, quest_id),
        )
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
            await message.answer(
                tr(language, "attempts_exhausted"),
                reply_markup=open_quest_keyboard(language, quest_id),
            )
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
        await message.answer(
            tr(language, "correct_done"),
            reply_markup=open_quest_keyboard(language, quest_id),
        )
        await db.maybe_complete_quest(quest_id)
        return
    if result.get("next_stage_order"):
        quest = await db.get_quest(quest_id)
        if quest:
            await notify_next_stage_ready(bot, db, quest, user_id)
        else:
            await message.answer(
                tr(
                    language,
                    "correct_next",
                    button=tr(language, "btn_continue_quest"),
                )
            )
        return
    await message.answer(tr(language, "correct_wait"))


@router.callback_query(F.data.startswith("support:pending:answer:"))
async def pending_text_as_answer(
    callback: CallbackQuery, state: FSMContext, db: Database, bot: Bot
) -> None:
    """Grade text that was written while the support form was open."""
    if not await ensure_private_callback(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    data = await state.get_data()
    pending = str(data.get("pending_support_text") or "").strip()
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.answer()
        return
    if not pending:
        await state.clear()
        await callback.answer(tr(language, "error_generic"), show_alert=True)
        return
    await state.clear()
    await callback.answer()
    if callback.message:
        await _process_answer(
            callback.message,
            quest_id,
            db,
            bot,
            answer_text=pending,
            actor_id=callback.from_user.id,
        )


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


@router.callback_query(F.data.startswith("quest:continue:"))
async def continue_quest_stage(
    callback: CallbackQuery, db: Database, bot: Bot
) -> None:
    """Send the participant's current question when Start/Continue is pressed."""
    if not await ensure_private_callback(callback, db):
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    quest = await db.get_quest(quest_id)
    participant = (
        await db.participant(quest_id, callback.from_user.id) if quest else None
    )
    if not quest or not participant or participant["status"] == "blocked":
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    if quest.get("paused_at"):
        await callback.answer(tr(language, "quest_paused_notice"), show_alert=True)
        return
    if quest["status"] != "active":
        key = "stage_not_started_yet" if quest["status"] == "scheduled" else "quest_ended"
        await callback.answer(tr(language, key), show_alert=True)
        return
    if participant["status"] not in {"joined", "active"}:
        await callback.answer(tr(language, "quest_ended"), show_alert=True)
        return
    stage = await db.next_deliverable_stage(
        quest_id, callback.from_user.id, utc_now()
    )
    if not stage:
        await callback.answer(
            tr(language, "quest_stage_unavailable"), show_alert=True
        )
        return
    already_activated = stage.get("session_status") == "open"
    me = await bot.get_me()
    delivered = await send_stage_to_user(
        bot,
        db,
        quest,
        stage,
        callback.from_user.id,
        me.username or "",
        already_activated=already_activated,
    )
    if not delivered:
        await callback.answer(
            tr(language, "quest_stage_unavailable"), show_alert=True
        )
        return
    await callback.answer(tr(language, "send_answer"))


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
        quest_id = await db.latest_live_quest_id(message.from_user.id)
        if quest_id is None:
            return
        quest = await db.get_quest(quest_id)
        started = bool(quest) and quest["status"] == "active"
        button_label = tr(
            language, "btn_continue_quest" if started else "btn_start_quest"
        )
        await message.answer(
            (
                tr(language, "stage_continue_hint", button=button_label)
                if started
                else tr(language, "quest_started_waiting")
            ),
            reply_markup=(
                continue_keyboard(language, quest_id, started) if started else None
            ),
        )
        return
    # A participant can have several unfinished quests: treat the message as an
    # answer to the question that was delivered most recently instead of asking
    # them to pick a quest (which used to send the text into the chat).
    await _process_answer(message, int(stages[0]["quest_id"]), db, bot)
