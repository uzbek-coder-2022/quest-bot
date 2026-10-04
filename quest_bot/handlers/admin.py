"""Quest administration and superadmin control panel handlers."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
)

from ..bot_commands import clear_app_admin_commands, set_app_admin_commands
from ..database import Database, utc_now
from ..keyboards import (
    admin_detail_keyboard,
    admin_home_keyboard,
    admin_list_keyboard,
    admin_panel,
    admin_quests_keyboard,
    button,
    confirm_delete_quest_keyboard,
    confirm_purge_quest_keyboard,
    manage_chat_keyboard,
    managed_chats_keyboard,
    open_quest_keyboard,
    page_sizes_keyboard,
    participants_keyboard,
    review_keyboard,
    settings_keyboard,
    whitelist_keyboard,
)
from ..localization import tr
from ..presentation import activity_message, information_message
from ..rich_text import heading, paragraph, preformatted, quote, rich_message
from ..services import (
    answer_deep_link,
    delete_archived_message,
    notify_next_stage_ready,
)
from ..states import ModerationFlow, SuperadminFlow
from ..utils import can_manage_quest, display_name, ensure_private_callback, safe_edit

router = Router(name="admin")


def _role_kb(language: str, quest_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(
                    tr(language, "btn_confirm"), f"manage:finish-confirmed:{quest_id}"
                ),
                button(
                    tr(language, "btn_cancel"),
                    f"manage:quest:{quest_id}",
                    style="danger",
                ),
            ],
            [button(tr(language, "btn_admin_home"), "admin:home")],
        ]
    )


def _chat_text(language: str, chat: dict) -> str:
    return tr(
        language,
        "chat_info",
        title=chat["title"],
        chat_id=chat["chat_id"],
        cleanup=tr(
            language, "cleanup_on" if chat["cleanup_enabled"] else "cleanup_off"
        ),
    )


def _chat_message(language: str, chat: dict) -> InputRichMessage:
    """Separate a managed-chat title from its translated details."""
    lines = _chat_text(language, chat).splitlines()
    return rich_message(
        heading(lines[0] if lines else chat["title"], size=1),
        paragraph("\n".join(lines[1:]))
        if len(lines) > 1
        else paragraph(chat["title"]),
    )


async def _require_superadmin(callback: CallbackQuery, db: Database) -> bool:
    if not await ensure_private_callback(callback, db):
        return False
    language = await db.get_language(callback.from_user.id)
    if await db.get_role(callback.from_user.id) != "superadmin":
        await callback.answer(tr(language, "superadmin_only"), show_alert=True)
        return False
    return True


async def _send_question_after_review(
    bot: Bot, db: Database, quest_id: int, user_id: int, stage_order: int
) -> None:
    """Ask a participant to open their next stage after a review approval."""
    quest = await db.get_quest(quest_id)
    if not quest:
        return
    await notify_next_stage_ready(bot, db, quest, user_id)


@router.message(Command("admin"))
async def admin_command(message: Message, db: Database) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    if message.chat.type != "private":
        await message.answer(tr(language, "open_private_chat"))
        return
    role = await db.get_role(message.from_user.id)
    if role not in {"admin", "superadmin"}:
        await message.answer(tr(language, "admin_only"))
        return
    await message.answer_rich(
        information_message(tr(language, "admin_panel")),
        reply_markup=admin_panel(language, role),
    )


@router.callback_query(F.data == "admin:home")
async def admin_home(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    if not await ensure_private_callback(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    role = await db.get_role(callback.from_user.id)
    if role not in {"admin", "superadmin"}:
        await callback.answer(tr(language, "admin_only"), show_alert=True)
        return
    await state.clear()
    if callback.message:
        await safe_edit(
            callback,
            information_message(tr(language, "admin_panel")),
            reply_markup=admin_panel(language, role),
        )
    await callback.answer()


@router.callback_query(F.data == "super:admins")
async def show_admins(callback: CallbackQuery, db: Database) -> None:
    if not await _require_superadmin(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    admins = await db.list_admins()
    rows = [
        f"{tr(language, 'role_admin' if admin['role'] == 'admin' else 'role_superadmin')} · "
        f"{admin.get('full_name') or admin.get('username') or '—'} · {admin['telegram_id']}"
        for admin in admins
    ]
    text = activity_message(tr(language, "admins_title"), rows)
    if callback.message:
        await safe_edit(
            callback, text, reply_markup=admin_list_keyboard(language, admins)
        )
    await callback.answer()


@router.callback_query(F.data == "super:addadmin")
async def add_admin_start(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    if not await _require_superadmin(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    await state.set_state(SuperadminFlow.add_admin_id)
    if callback.message:
        await callback.message.answer(
            tr(language, "ask_admin_id"), reply_markup=admin_home_keyboard(language)
        )
    await callback.answer()


@router.message(SuperadminFlow.add_admin_id)
async def add_admin_id_received(
    message: Message, state: FSMContext, db: Database
) -> None:
    language = await db.get_language(message.from_user.id)
    try:
        admin_id = int((message.text or "").strip())
    except ValueError:
        await message.answer(tr(language, "invalid_admin_id"))
        return
    if admin_id <= 0:
        await message.answer(tr(language, "invalid_admin_id"))
        return
    added = await db.add_admin(admin_id, message.from_user.id)
    await state.clear()
    if added:
        await set_app_admin_commands(message.bot, admin_id)
        await db.log_action(message.from_user.id, "admin.added", "admin", admin_id)
        await message.answer(
            tr(language, "admin_added"), reply_markup=admin_home_keyboard(language)
        )
        try:
            target_language = await db.get_language(admin_id)
            await message.bot.send_message(
                admin_id,
                tr(target_language, "admin_panel"),
                reply_markup=admin_panel(target_language, "admin"),
            )
        except TelegramAPIError:
            pass
    else:
        await message.answer(
            tr(language, "admin_only"), reply_markup=admin_home_keyboard(language)
        )


@router.callback_query(F.data.startswith("super:removeadmin:"))
async def remove_admin_callback(
    callback: CallbackQuery, db: Database, bot: Bot
) -> None:
    if not await _require_superadmin(callback, db):
        return
    try:
        admin_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    removed = await db.remove_admin(admin_id)
    language = await db.get_language(callback.from_user.id)
    if removed:
        await clear_app_admin_commands(bot, admin_id)
        await db.log_action(callback.from_user.id, "admin.removed", "admin", admin_id)
        await callback.answer(tr(language, "admin_removed"), show_alert=True)
    else:
        await callback.answer(tr(language, "superadmin_only"), show_alert=True)
    admins = await db.list_admins()
    if callback.message:
        rows = [
            f"{tr(language, 'role_admin' if item['role'] == 'admin' else 'role_superadmin')} · "
            f"{item.get('full_name') or item.get('username') or '—'} · {item['telegram_id']}"
            for item in admins
        ]
        text = activity_message(tr(language, "admins_title"), rows)
        await safe_edit(
            callback, text, reply_markup=admin_list_keyboard(language, admins)
        )


def _admin_name(admin: dict) -> str:
    return str(
        admin.get("full_name") or admin.get("username") or admin["telegram_id"]
    )


async def _find_admin(db: Database, admin_id: int) -> dict | None:
    for admin in await db.list_admins():
        if int(admin["telegram_id"]) == admin_id:
            return admin
    return None


@router.callback_query(F.data.regexp(r"^super:admin:-?\d+$"))
async def show_admin_detail(callback: CallbackQuery, db: Database) -> None:
    """Show one administrator with their quests and contact actions."""
    if not await _require_superadmin(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    admin_id = int(callback.data.rsplit(":", 1)[1])
    admin = await _find_admin(db, admin_id)
    if not admin:
        await callback.answer(tr(language, "superadmin_only"), show_alert=True)
        return
    quests = await db.list_owner_quests(admin_id, include_deleted=True)
    deleted = sum(1 for quest in quests if quest["deleted"])
    body = tr(
        language,
        "admin_detail_body",
        role=tr(language, f"role_{admin['role']}"),
        user_id=admin_id,
        quests=len(quests),
        deleted=deleted,
    )
    if callback.message:
        await safe_edit(
            callback,
            information_message(
                tr(language, "admin_detail_title", name=_admin_name(admin)), body
            ),
            reply_markup=admin_detail_keyboard(language, admin, len(quests)),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("super:adminquests:"))
async def show_admin_quests(callback: CallbackQuery, db: Database) -> None:
    """List every quest of one administrator, including deleted ones."""
    if not await _require_superadmin(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer()
        return
    try:
        owner_id = int(parts[2])
        page = max(0, int(parts[3]))
    except ValueError:
        await callback.answer()
        return
    page_size = int(await db.settings_get("page_size", "10"))
    admin = await _find_admin(db, owner_id)
    name = _admin_name(admin) if admin else str(owner_id)
    quests = await db.list_owner_quests(owner_id, include_deleted=True)
    window = quests[page * page_size : (page + 1) * page_size]
    lines = []
    for quest in window:
        status_label = tr(language, "filter_" + str(quest["status"]))
        marker = "🗑" if quest["deleted"] else "🧭"
        lines.append(f"{marker} {quest['title']} · {status_label}")
    if callback.message:
        await safe_edit(
            callback,
            activity_message(
                tr(language, "admin_quests_title", name=name),
                lines,
                None if lines else tr(language, "admin_quests_empty"),
            ),
            reply_markup=admin_quests_keyboard(
                language, owner_id, window, page, page_size
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("super:adminmsg:"))
async def begin_admin_message(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    """Ask the superadmin what to write to one administrator."""
    if not await _require_superadmin(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    try:
        admin_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    admin = await _find_admin(db, admin_id)
    if not admin:
        await callback.answer(tr(language, "superadmin_only"), show_alert=True)
        return
    if await state.get_state() is not None:
        await callback.answer(
            tr(language, "participant_message_finish_current_flow"), show_alert=True
        )
        return
    await state.set_state(SuperadminFlow.admin_message)
    await state.update_data(admin_message_to=admin_id)
    if callback.message:
        await callback.message.answer(
            tr(language, "ask_admin_message", name=_admin_name(admin)),
            reply_markup=admin_home_keyboard(language),
        )
    await callback.answer()


@router.message(SuperadminFlow.admin_message)
async def admin_message_received(
    message: Message, state: FSMContext, db: Database
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    if await db.get_role(message.from_user.id) != "superadmin":
        await state.clear()
        await message.answer(tr(language, "superadmin_only"))
        return
    text = (message.text or "").strip()
    if not 1 <= len(text) <= 2000:
        await message.answer(tr(language, "invalid_participant_message"))
        return
    data = await state.get_data()
    admin_id = int(data.get("admin_message_to") or 0)
    await state.clear()
    admin = await _find_admin(db, admin_id)
    if not admin:
        await message.answer(tr(language, "superadmin_only"))
        return
    name = _admin_name(admin)
    target_language = await db.get_language(admin_id)
    try:
        await message.bot.send_message(
            admin_id,
            f"{tr(target_language, 'admin_message_heading')}\n\n{text}",
        )
        sent = True
    except TelegramAPIError:
        sent = False
    await db.log_action(
        message.from_user.id, "admin.message.sent", "admin", admin_id
    )
    await message.answer(
        tr(
            language,
            "admin_message_sent" if sent else "participant_message_delivery_failed",
            name=name,
        ),
        reply_markup=admin_home_keyboard(language),
    )


@router.callback_query(F.data.startswith("manage:purge:"))
async def confirm_quest_purge(callback: CallbackQuery, db: Database) -> None:
    """Ask a superadmin to confirm erasing a deleted quest for good."""
    if not await ensure_private_callback(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    if await db.get_role(callback.from_user.id) != "superadmin":
        await callback.answer(tr(language, "superadmin_only"), show_alert=True)
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.answer()
        return
    quest = await db.get_quest(quest_id, include_deleted=True)
    if not quest or not quest["deleted"]:
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    if callback.message:
        await callback.message.answer(
            tr(language, "confirm_purge_quest", title=quest["title"]),
            reply_markup=confirm_purge_quest_keyboard(language, quest_id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:purgeconfirm:"))
async def purge_quest_confirmed(
    callback: CallbackQuery, db: Database, bot: Bot
) -> None:
    """Erase a soft-deleted quest and all of its data from the database."""
    if not await ensure_private_callback(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    if await db.get_role(callback.from_user.id) != "superadmin":
        await callback.answer(tr(language, "superadmin_only"), show_alert=True)
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.answer()
        return
    quest = await db.get_quest(quest_id, include_deleted=True)
    if not quest or not quest["deleted"]:
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    title = quest["title"]
    references = await db.quest_archive_references(quest_id)
    if not await db.purge_quest(quest_id, callback.from_user.id):
        await callback.answer(tr(language, "error_generic"), show_alert=True)
        return
    # The quest rows are gone; archived copies are best-effort cleanup.
    for chat_id, message_id in references:
        await delete_archived_message(bot, chat_id, message_id)
    if callback.message:
        await safe_edit(
            callback,
            tr(language, "quest_purged", title=title),
            reply_markup=admin_home_keyboard(language),
        )
    await callback.answer()


@router.callback_query(F.data == "super:noop")
async def superadmin_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == "super:stats")
async def show_stats(callback: CallbackQuery, db: Database) -> None:
    if not await _require_superadmin(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    stats = await db.statistics()
    if callback.message:
        await safe_edit(
            callback,
            rich_message(
                heading(tr(language, "btn_stats"), size=1),
                paragraph(tr(language, "stats", **stats)),
            ),
            reply_markup=admin_home_keyboard(language),
        )
    await callback.answer()


def _terminal_log_lines(rows: list[dict]) -> str:
    """Format audit rows as aligned, monospaced terminal output."""
    lines = []
    for row in rows:
        stamp = str(row.get("created_at") or "")[:19].replace("T", " ")
        action = str(row.get("action") or "-")[:32].ljust(32)
        entity = f"{row.get('entity_type') or '-'}:{row.get('entity_id') or '-'}"
        actor = f"@{row.get('actor_id')}" if row.get("actor_id") else "@system"
        lines.append(f"{stamp}  {action}  {entity}  {actor}")
    return "\n".join(lines)


@router.callback_query(F.data == "super:logs")
async def show_logs(callback: CallbackQuery, db: Database) -> None:
    if not await _require_superadmin(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    rows = await db.latest_logs(20)
    refresh = tr(language, "btn_refresh")
    blocks = [
        heading(f"🖥 {tr(language, 'logs_title')}", size=1),
        paragraph(
            tr(language, "logs_terminal_hint", count=len(rows), button=refresh)
        ),
    ]
    if rows:
        blocks.append(preformatted(_terminal_log_lines(rows), language="bash"))
    else:
        blocks.append(paragraph(tr(language, "no_logs")))
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [button(refresh, "super:logs")],
            [button(tr(language, "btn_admin_home"), "admin:home")],
        ]
    )
    if callback.message:
        await safe_edit(callback, rich_message(*blocks), reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "super:settings")
async def show_settings(callback: CallbackQuery, db: Database) -> None:
    if not await _require_superadmin(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    size = int(await db.settings_get("page_size", "10"))
    text = tr(language, "settings_page_size", size=size)
    if callback.message:
        await safe_edit(callback, text, reply_markup=settings_keyboard(language, size))
    await callback.answer()


@router.callback_query(F.data == "super:pagesize")
async def choose_page_size(callback: CallbackQuery, db: Database) -> None:
    if not await _require_superadmin(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    current = int(await db.settings_get("page_size", "10"))
    if callback.message:
        await safe_edit(
            callback,
            tr(language, "choose_page_size"),
            reply_markup=page_sizes_keyboard(language, current),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("super:pagesize:"))
async def set_page_size(callback: CallbackQuery, db: Database) -> None:
    if not await _require_superadmin(callback, db):
        return
    try:
        size = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    if size not in {5, 10, 20, 50}:
        await callback.answer()
        return
    await db.settings_set("page_size", str(size))
    await db.log_action(
        callback.from_user.id,
        "settings.page_size.updated",
        "setting",
        "page_size",
        {"value": size},
    )
    language = await db.get_language(callback.from_user.id)
    if callback.message:
        await safe_edit(
            callback,
            tr(language, "settings_page_size", size=size),
            reply_markup=settings_keyboard(language, size),
        )
    await callback.answer()


@router.callback_query(F.data == "super:export")
async def export_data(callback: CallbackQuery, db: Database) -> None:
    if not await _require_superadmin(callback, db):
        return
    data = await db.export_zip()
    await callback.message.answer_document(
        BufferedInputFile(data, filename="quest-bot-export.zip")
    )
    await callback.answer()


@router.callback_query(F.data == "super:chats")
async def show_managed_chats(callback: CallbackQuery, db: Database) -> None:
    if not await _require_superadmin(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    chats = await db.managed_chats()
    text = information_message(
        tr(language, "btn_chats"), None if chats else tr(language, "chats_empty")
    )
    if callback.message:
        await safe_edit(
            callback, text, reply_markup=managed_chats_keyboard(language, chats)
        )
    await callback.answer()


@router.callback_query(F.data == "super:addchat")
async def add_chat_start(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    if not await _require_superadmin(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    await state.set_state(SuperadminFlow.add_chat_id)
    if callback.message:
        await callback.message.answer(
            tr(language, "ask_chat_id"), reply_markup=admin_home_keyboard(language)
        )
    await callback.answer()


@router.message(SuperadminFlow.add_chat_id)
async def add_chat_id_received(
    message: Message, state: FSMContext, db: Database, bot: Bot
) -> None:
    language = await db.get_language(message.from_user.id)
    try:
        chat_id = int((message.text or "").strip())
        chat = await bot.get_chat(chat_id)
        bot_user = await bot.get_me()
        bot_member = await bot.get_chat_member(chat_id, bot_user.id)
    except (ValueError, TelegramAPIError):
        await message.answer(tr(language, "chat_not_found"))
        return
    if chat.type not in {"group", "supergroup", "channel"} or bot_member.status not in {
        "administrator",
        "creator",
    }:
        await message.answer(tr(language, "chat_not_found"))
        return
    if (
        chat.type == "channel"
        and bot_member.status != "creator"
        and not getattr(bot_member, "can_post_messages", False)
    ):
        await message.answer(tr(language, "chat_permission_missing"))
        return
    title = chat.title or str(chat_id)
    await db.register_chat(chat_id, title, chat.type, message.from_user.id)
    await db.log_action(
        message.from_user.id, "managed_chat.added", "chat", chat_id, {"title": title}
    )
    await state.clear()
    await message.answer(
        tr(language, "chat_added", title=title),
        reply_markup=admin_home_keyboard(language),
    )


@router.callback_query(F.data.startswith("super:chat:"))
async def show_chat(callback: CallbackQuery, db: Database) -> None:
    if not await _require_superadmin(callback, db):
        return
    try:
        chat_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    chat = await db.get_managed_chat(chat_id)
    language = await db.get_language(callback.from_user.id)
    if not chat:
        await callback.answer(tr(language, "chat_not_found"), show_alert=True)
        return
    if callback.message:
        await safe_edit(
            callback,
            _chat_message(language, chat),
            reply_markup=manage_chat_keyboard(language, chat),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("super:cleanup:"))
async def toggle_cleanup(callback: CallbackQuery, db: Database, bot: Bot) -> None:
    if not await _require_superadmin(callback, db):
        return
    try:
        chat_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    configured_chat = await db.get_managed_chat(chat_id)
    if configured_chat and not configured_chat["cleanup_enabled"]:
        try:
            bot_member = await bot.get_chat_member(chat_id, (await bot.get_me()).id)
        except TelegramAPIError:
            await callback.answer(
                tr(language, "chat_permission_missing"), show_alert=True
            )
            return
        can_restrict = bot_member.status == "creator" or bool(
            getattr(bot_member, "can_restrict_members", False)
        )
        if not can_restrict:
            await callback.answer(
                tr(language, "chat_permission_missing"), show_alert=True
            )
            return
    value = await db.toggle_chat_cleanup(chat_id)
    if value is None:
        await callback.answer(tr(language, "chat_not_found"), show_alert=True)
        return
    await db.log_action(
        callback.from_user.id,
        "chat.cleanup.toggled",
        "chat",
        chat_id,
        {"enabled": value},
    )
    chat = await db.get_managed_chat(chat_id)
    if callback.message and chat:
        await safe_edit(
            callback,
            _chat_message(language, chat),
            reply_markup=manage_chat_keyboard(language, chat),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("super:whitelist:"))
async def show_whitelist(callback: CallbackQuery, db: Database) -> None:
    if not await _require_superadmin(callback, db):
        return
    try:
        chat_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    users = await db.chat_whitelist(chat_id)
    text = information_message(
        tr(language, "whitelist_title"), None if users else tr(language, "no_whitelist")
    )
    if callback.message:
        await safe_edit(
            callback, text, reply_markup=whitelist_keyboard(language, chat_id, users)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("super:addwhite:"))
async def add_whitelist_start(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    if not await _require_superadmin(callback, db):
        return
    try:
        chat_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    await state.update_data(whitelist_chat_id=chat_id)
    await state.set_state(SuperadminFlow.add_whitelist_id)
    if callback.message:
        await callback.message.answer(tr(language, "ask_whitelist_id"))
    await callback.answer()


@router.message(SuperadminFlow.add_whitelist_id)
async def whitelist_id_received(
    message: Message, state: FSMContext, db: Database
) -> None:
    language = await db.get_language(message.from_user.id)
    try:
        user_id = int((message.text or "").strip())
    except ValueError:
        await message.answer(tr(language, "invalid_admin_id"))
        return
    if user_id <= 0:
        await message.answer(tr(language, "invalid_admin_id"))
        return
    data = await state.get_data()
    chat_id = int(data["whitelist_chat_id"])
    await db.add_chat_whitelist(chat_id, user_id, message.from_user.id)
    await db.log_action(
        message.from_user.id,
        "chat.whitelist.added",
        "chat_member",
        f"{chat_id}:{user_id}",
    )
    await state.clear()
    users = await db.chat_whitelist(chat_id)
    await message.answer(
        tr(language, "whitelist_added"),
        reply_markup=whitelist_keyboard(language, chat_id, users),
    )


@router.callback_query(F.data.startswith("super:delwhite:"))
async def remove_whitelist(callback: CallbackQuery, db: Database) -> None:
    if not await _require_superadmin(callback, db):
        return
    try:
        _, _, chat_id_text, user_id_text = callback.data.split(":", 3)
        chat_id, user_id = int(chat_id_text), int(user_id_text)
    except ValueError:
        await callback.answer()
        return
    await db.remove_chat_whitelist(chat_id, user_id)
    await db.log_action(
        callback.from_user.id,
        "chat.whitelist.removed",
        "chat_member",
        f"{chat_id}:{user_id}",
    )
    language = await db.get_language(callback.from_user.id)
    users = await db.chat_whitelist(chat_id)
    if callback.message:
        await safe_edit(
            callback,
            information_message(tr(language, "whitelist_title")),
            reply_markup=whitelist_keyboard(language, chat_id, users),
        )
    await callback.answer(tr(language, "whitelist_removed"))


@router.callback_query(F.data.startswith("manage:participants:"))
async def show_participants(callback: CallbackQuery, db: Database) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        parts = callback.data.split(":")
        quest_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except ValueError:
        await callback.answer()
        return
    quest = await db.get_quest(quest_id)
    language = await db.get_language(callback.from_user.id)
    if not quest or not await can_manage_quest(db, callback.from_user.id, quest):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    participants, total, page = await db.list_participants_page(quest_id, page, 20)
    title = tr(language, "participants_title")
    if not total:
        text = information_message(title, tr(language, "no_participants"))
    else:
        start = page * 20
        text = information_message(
            title,
            f"{start + 1}–{min(start + len(participants), total)}/{total}",
        )
    if callback.message:
        await safe_edit(
            callback,
            text,
            reply_markup=participants_keyboard(
                language,
                quest_id,
                participants,
                page,
                20,
                show_message_button=True,
                total_count=total,
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:participantmsg:"))
async def begin_participant_message(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        _, _, quest_text, user_text, page_text = callback.data.split(":", 4)
        quest_id, user_id, page = int(quest_text), int(user_text), max(0, int(page_text))
    except ValueError:
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    quest = await db.get_quest(quest_id)
    participant = await db.participant(quest_id, user_id)
    if (
        not quest
        or not participant
        or not await can_manage_quest(db, callback.from_user.id, quest)
    ):
        await callback.answer(tr(language, "participant_message_unavailable"), show_alert=True)
        return
    if await state.get_state() is not None:
        await callback.answer(
            tr(language, "participant_message_finish_current_flow"), show_alert=True
        )
        return
    user = await db.get_user(user_id) or {}
    user_name = display_name(user.get("full_name"), user.get("username"), user_id)
    await state.update_data(
        participant_message_quest_id=quest_id,
        participant_message_user_id=user_id,
        participant_message_page=page,
    )
    await state.set_state(SuperadminFlow.participant_message)
    if callback.message:
        await callback.message.answer(
            tr(
                language,
                "ask_participant_message",
                user=user_name,
                title=quest["title"],
            ),
            reply_markup=admin_home_keyboard(language),
        )
    await callback.answer()


@router.message(SuperadminFlow.participant_message)
async def participant_message_received(
    message: Message, state: FSMContext, db: Database, bot: Bot
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    role = await db.get_role(message.from_user.id)
    if role not in {"admin", "superadmin"}:
        await state.clear()
        await message.answer(tr(language, "admin_only"))
        return
    text = (message.text or "").strip()
    if not 1 <= len(text) <= 2000:
        await message.answer(tr(language, "invalid_participant_message"))
        return
    data = await state.get_data()
    try:
        quest_id = int(data["participant_message_quest_id"])
        user_id = int(data["participant_message_user_id"])
        page = max(0, int(data.get("participant_message_page", 0)))
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await message.answer(tr(language, "error_generic"))
        return
    quest = await db.get_quest(quest_id)
    participant = await db.participant(quest_id, user_id) if quest else None
    if (
        not quest
        or not participant
        or not await can_manage_quest(db, message.from_user.id, quest)
    ):
        await state.clear()
        await message.answer(tr(language, "participant_message_unavailable"))
        return
    heading_key = (
        "participant_message_heading"
        if role == "superadmin"
        else "participant_message_heading_admin"
    )
    target_user = await db.get_user(user_id) or {}
    target_name = display_name(
        target_user.get("full_name"), target_user.get("username"), user_id
    )
    target_language = await db.get_language(user_id)
    try:
        await bot.send_rich_message(
            user_id,
            rich_message(
                heading(
                    tr(
                        target_language,
                        heading_key,
                        title=quest["title"],
                    ),
                    size=2,
                ),
                paragraph(text),
            ),
        )
    except TelegramAPIError:
        await state.clear()
        await message.answer(tr(language, "participant_message_delivery_failed"))
        return
    await db.log_action(
        message.from_user.id,
        "participant.message.sent",
        "quest_participant",
        f"{quest_id}:{user_id}",
    )
    await state.clear()
    await message.answer(
        tr(language, "participant_message_sent", user=target_name),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [button(tr(language, "btn_back"), f"manage:participants:{quest_id}:{page}")],
                [button(tr(language, "btn_admin_home"), "admin:home")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("manage:participant:"))
async def participant_moderation(
    callback: CallbackQuery, state: FSMContext, db: Database, bot: Bot
) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        _, _, quest_text, user_text, action = callback.data.split(":", 4)
        quest_id, user_id = int(quest_text), int(user_text)
    except ValueError:
        await callback.answer()
        return
    quest = await db.get_quest(quest_id)
    language = await db.get_language(callback.from_user.id)
    if not quest or not await can_manage_quest(db, callback.from_user.id, quest):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    if action == "unban":
        success = await db.set_participant_block(quest_id, user_id, None, False)
        if success:
            await db.log_action(
                callback.from_user.id,
                "participant.unblocked",
                "quest_participant",
                f"{quest_id}:{user_id}",
            )
            try:
                target_language = await db.get_language(user_id)
                await bot.send_message(
                    user_id, tr(target_language, "unblocked_success")
                )
            except TelegramAPIError:
                pass
            await callback.answer(tr(language, "unblocked_success"), show_alert=True)
        else:
            await callback.answer(tr(language, "error_generic"), show_alert=True)
        return
    if action != "ban":
        await callback.answer()
        return
    await state.update_data(moderation_quest_id=quest_id, moderation_user_id=user_id)
    await state.set_state(ModerationFlow.ban_reason)
    if callback.message:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [button(tr(language, "btn_skip"), "moderation:skip")],
                [button(tr(language, "btn_admin_home"), "admin:home")],
            ]
        )
        await callback.message.answer(tr(language, "ask_ban_reason"), reply_markup=kb)
    await callback.answer()


async def _apply_participant_ban(
    message: Message, state: FSMContext, db: Database, bot: Bot, reason: str | None
) -> None:
    data = await state.get_data()
    quest_id = int(data["moderation_quest_id"])
    user_id = int(data["moderation_user_id"])
    quest = await db.get_quest(quest_id)
    language = await db.get_language(message.from_user.id)
    if not quest or not await can_manage_quest(db, message.from_user.id, quest):
        await state.clear()
        await message.answer(tr(language, "quest_not_found"))
        return
    success = await db.set_participant_block(quest_id, user_id, reason, True)
    if success:
        await db.log_action(
            message.from_user.id,
            "participant.blocked",
            "quest_participant",
            f"{quest_id}:{user_id}",
            {"reason": reason},
        )
        try:
            target_language = await db.get_language(user_id)
            reason_line = (
                tr(target_language, "reason_line", reason=reason) if reason else ""
            )
            await bot.send_message(
                user_id,
                tr(
                    target_language,
                    "warning_blocked",
                    title=quest["title"],
                    reason=reason_line,
                ),
            )
        except TelegramAPIError:
            pass
    await state.clear()
    await message.answer(
        tr(language, "blocked_success" if success else "error_generic"),
        reply_markup=admin_home_keyboard(language),
    )


@router.message(ModerationFlow.ban_reason)
async def ban_reason_received(
    message: Message, state: FSMContext, db: Database, bot: Bot
) -> None:
    text = (message.text or "").strip()
    if len(text) > 300:
        language = await db.get_language(message.from_user.id)
        await message.answer(tr(language, "invalid_text"))
        return
    await _apply_participant_ban(
        message, state, db, bot, text if text and text != "-" else None
    )


@router.callback_query(F.data == "moderation:skip")
async def skip_ban_reason(
    callback: CallbackQuery, state: FSMContext, db: Database, bot: Bot
) -> None:
    if not await ensure_private_callback(callback, db):
        return
    if await state.get_state() != ModerationFlow.ban_reason.state:
        await callback.answer()
        return
    data = await state.get_data()
    quest = await db.get_quest(int(data["moderation_quest_id"]))
    language = await db.get_language(callback.from_user.id)
    if not quest or not await can_manage_quest(db, callback.from_user.id, quest):
        await state.clear()
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    await db.set_participant_block(
        int(data["moderation_quest_id"]), int(data["moderation_user_id"]), None, True
    )
    await db.log_action(
        callback.from_user.id,
        "participant.blocked",
        "quest_participant",
        f"{data['moderation_quest_id']}:{data['moderation_user_id']}",
        {"reason": None},
    )
    target_language = await db.get_language(int(data["moderation_user_id"]))
    try:
        await bot.send_message(
            int(data["moderation_user_id"]),
            tr(target_language, "warning_blocked", title=quest["title"], reason=""),
        )
    except TelegramAPIError:
        pass
    await state.clear()
    if callback.message:
        await callback.message.answer(
            tr(language, "blocked_success"), reply_markup=admin_home_keyboard(language)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:pending:"))
async def show_pending_answers(callback: CallbackQuery, db: Database, bot: Bot) -> None:
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
    pending = await db.pending_answers(quest_id)
    if not pending:
        if callback.message:
            await callback.message.answer(tr(language, "no_pending"))
    else:
        for item in pending[:25]:
            name = display_name(
                item.get("full_name"), item.get("username"), item["user_id"]
            )
            title = tr(
                language,
                "review_item",
                name=name,
                stage=item["stage_order"],
                answer="",
            ).rstrip(":\n")
            await bot.send_rich_message(
                callback.from_user.id,
                rich_message(heading(title, size=2), quote(item["answer_text"])),
                reply_markup=review_keyboard(language, item["answer_id"]),
            )
    await callback.answer()


@router.callback_query(F.data.startswith("review:"))
async def review_answer(callback: CallbackQuery, db: Database, bot: Bot) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        _, answer_text, decision = callback.data.split(":", 2)
        answer_id = int(answer_text)
    except ValueError:
        await callback.answer()
        return
    item = await db.get_pending_answer(answer_id)
    language = await db.get_language(callback.from_user.id)
    if not item:
        await callback.answer(tr(language, "no_pending"), show_alert=True)
        return
    quest = await db.get_quest(int(item["quest_id"]))
    if not quest or not await can_manage_quest(db, callback.from_user.id, quest):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    accepted = decision == "yes"
    result = await db.review_answer(
        answer_id, callback.from_user.id, accepted, utc_now()
    )
    if result["code"] == "paused":
        await callback.answer(tr(language, "quest_paused_notice"), show_alert=True)
        return
    if result["code"] != "reviewed":
        await callback.answer(tr(language, "no_pending"), show_alert=True)
        return
    await db.log_action(
        callback.from_user.id,
        "answer.reviewed",
        "answer",
        answer_id,
        {"accepted": accepted},
    )
    target_language = await db.get_language(int(item["user_id"]))
    await bot.send_message(
        int(item["user_id"]),
        tr(
            target_language,
            "review_to_user",
            result=tr(
                target_language, "correct_result" if accepted else "wrong_result"
            ),
        ),
    )
    if accepted and result.get("final"):
        await bot.send_message(
            int(item["user_id"]),
            tr(target_language, "correct_done"),
            reply_markup=open_quest_keyboard(
                target_language, int(item["quest_id"])
            ),
        )
    elif accepted and result.get("next_stage_order"):
        # The next-stage notice already carries a Continue button.
        await _send_question_after_review(
            bot,
            db,
            int(item["quest_id"]),
            int(item["user_id"]),
            int(result["next_stage_order"]),
        )
    elif not accepted and result.get("exhausted"):
        await bot.send_message(
            int(item["user_id"]),
            tr(target_language, "attempts_exhausted"),
            reply_markup=open_quest_keyboard(
                target_language, int(item["quest_id"])
            ),
        )
    elif not accepted and not result.get("obsolete"):
        await bot.send_message(
            int(item["user_id"]),
            tr(target_language, "wrong_answer", remaining=result.get("remaining", 0)),
        )
    if result.get("final"):
        await db.maybe_complete_quest(int(item["quest_id"]))
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass
    await callback.answer(
        tr(language, "review_approved" if accepted else "review_rejected"),
        show_alert=True,
    )


@router.callback_query(F.data.startswith("manage:finish-confirm:"))
async def ask_finish_confirmation(callback: CallbackQuery, db: Database) -> None:
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
    if callback.message:
        await callback.message.answer(
            tr(language, "confirm_finish"), reply_markup=_role_kb(language, quest_id)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:finish-confirmed:"))
async def finish_quest_callback(
    callback: CallbackQuery, db: Database, bot: Bot
) -> None:
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
    user_ids = await db.set_quest_status(quest_id, "completed")
    await db.log_action(callback.from_user.id, "quest.finished", "quest", quest_id)
    for user_id in user_ids:
        try:
            await bot.send_message(
                user_id, tr(await db.get_language(user_id), "quest_ended")
            )
        except TelegramAPIError:
            pass
    if callback.message:
        await safe_edit(
            callback,
            tr(language, "quest_finished"),
            reply_markup=admin_home_keyboard(language),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:archive:"))
async def archive_quest_callback(
    callback: CallbackQuery, db: Database, bot: Bot
) -> None:
    if not await ensure_private_callback(callback, db):
        return
    if await db.get_role(callback.from_user.id) != "superadmin":
        await callback.answer(
            tr(await db.get_language(callback.from_user.id), "superadmin_only"),
            show_alert=True,
        )
        return
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
    user_ids = await db.set_quest_status(quest_id, "archived")
    await db.log_action(callback.from_user.id, "quest.archived", "quest", quest_id)
    for user_id in user_ids:
        try:
            await bot.send_message(
                user_id, tr(await db.get_language(user_id), "quest_ended")
            )
        except TelegramAPIError:
            pass
    if callback.message:
        await safe_edit(
            callback,
            tr(language, "quest_archived"),
            reply_markup=admin_home_keyboard(language),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:unarchive:"))
async def unarchive_quest_callback(callback: CallbackQuery, db: Database) -> None:
    if not await ensure_private_callback(callback, db):
        return
    if await db.get_role(callback.from_user.id) != "superadmin":
        await callback.answer(
            tr(await db.get_language(callback.from_user.id), "superadmin_only"),
            show_alert=True,
        )
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    quest = await db.get_quest(quest_id)
    language = await db.get_language(callback.from_user.id)
    if not quest or quest["status"] != "archived":
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    if quest.get("completed_at"):
        status = "completed"
    else:
        status = "active" if quest["start_at"] <= utc_now() else "scheduled"
    await db.set_quest_status(quest_id, status)
    await db.log_action(
        callback.from_user.id, "quest.unarchived", "quest", quest_id, {"status": status}
    )
    if callback.message:
        await safe_edit(
            callback,
            tr(language, "quest_unarchived"),
            reply_markup=admin_home_keyboard(language),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:delete:"))
async def confirm_quest_deletion(callback: CallbackQuery, db: Database) -> None:
    """Ask before soft-deleting a quest: only the delete flag is set."""
    if not await ensure_private_callback(callback, db):
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    quest = await db.get_quest(quest_id)
    role = await db.get_role(callback.from_user.id)
    if not quest or not await can_manage_quest(db, callback.from_user.id, quest):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    if quest["status"] == "archived" and role != "superadmin":
        await callback.answer(tr(language, "quest_metadata_archived"), show_alert=True)
        return
    if callback.message:
        await callback.message.answer(
            tr(language, "confirm_delete_quest", title=quest["title"]),
            reply_markup=confirm_delete_quest_keyboard(language, quest_id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:deleteconfirm:"))
async def delete_quest_confirmed(callback: CallbackQuery, db: Database) -> None:
    """Mark a quest deleted without removing its data."""
    if not await ensure_private_callback(callback, db):
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    quest = await db.get_quest(quest_id)
    role = await db.get_role(callback.from_user.id)
    if not quest or not await can_manage_quest(db, callback.from_user.id, quest):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    if quest["status"] == "archived" and role != "superadmin":
        await callback.answer(tr(language, "quest_metadata_archived"), show_alert=True)
        return
    if not await db.soft_delete_quest(quest_id, callback.from_user.id, utc_now()):
        await callback.answer(tr(language, "error_generic"), show_alert=True)
        return
    if callback.message:
        await safe_edit(
            callback,
            tr(language, "quest_deleted"),
            reply_markup=admin_home_keyboard(language),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:restore:"))
async def restore_quest_callback(callback: CallbackQuery, db: Database) -> None:
    """Let a superadmin clear the soft-delete flag again."""
    if not await ensure_private_callback(callback, db):
        return
    language = await db.get_language(callback.from_user.id)
    if await db.get_role(callback.from_user.id) != "superadmin":
        await callback.answer(tr(language, "superadmin_only"), show_alert=True)
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.answer()
        return
    quest = await db.get_quest(quest_id, include_deleted=True)
    if not quest or not quest["deleted"]:
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    if not await db.restore_quest(quest_id, callback.from_user.id, utc_now()):
        await callback.answer(tr(language, "error_generic"), show_alert=True)
        return
    if callback.message:
        await safe_edit(
            callback,
            tr(language, "quest_restored"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        button(
                            tr(language, "btn_open"),
                            f"manage:quest:{quest_id}",
                        )
                    ],
                    [
                        button(
                            tr(language, "btn_back"), "adminq:filter:deleted:0"
                        )
                    ],
                    [button(tr(language, "btn_admin_home"), "admin:home")],
                ]
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manage:privateinvite:"))
async def private_invite_link(callback: CallbackQuery, db: Database, bot: Bot) -> None:
    if not await ensure_private_callback(callback, db):
        return
    try:
        quest_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    quest = await db.get_quest(quest_id)
    language = await db.get_language(callback.from_user.id)
    if (
        not quest
        or quest["visibility"] != "private"
        or not await can_manage_quest(db, callback.from_user.id, quest)
    ):
        await callback.answer(tr(language, "quest_not_found"), show_alert=True)
        return
    me = await bot.get_me()
    link = answer_deep_link(me.username or "", quest)
    if callback.message:
        await callback.message.answer(link)
    await callback.answer()
