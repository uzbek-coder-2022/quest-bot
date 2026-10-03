"""Inline keyboard builders."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .localization import LANGUAGE_NAMES, tr


def _button_style(data: str) -> str:
    """Choose Telegram's built-in semantic button color from callback behavior."""
    if (
        data in {"create:cancel", "manage:editcancel"}
        or ":cancel:" in data
        or data.endswith(":cancel")
        or data.startswith((
            "quest:joincancel:",
            "manage:archive:",
            "manage:finish-confirmed:",
            "manage:edit:cover:remove",
            "super:removeadmin:",
            "super:delwhite:",
        ))
        or (data.startswith("review:") and data.endswith(":no"))
        or (data.startswith("manage:participant:") and data.endswith(":ban"))
    ):
        return "danger"
    if (
        data.startswith((
            "quest:joinconfirm:",
            "manage:resume:",
            "manage:unarchive:",
            "super:addadmin",
            "super:addchat",
            "super:addwhite:",
            "create:start",
        ))
        or (data.startswith("review:") and data.endswith(":yes"))
        or (data.startswith("manage:participant:") and data.endswith(":unban"))
    ):
        return "success"
    return "primary"


def button(text: str, data: str, style: str | None = None) -> InlineKeyboardButton:
    """Create an inline button with Telegram's native blue/green/red style."""
    selected_style = style or _button_style(data)
    if selected_style not in {"primary", "success", "danger"}:
        raise ValueError("Button style must be primary, success, or danger")
    return InlineKeyboardButton(text=text, callback_data=data, style=selected_style)


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[button(LANGUAGE_NAMES[language], f"lang:{language}") for language in ("uz", "ru", "en")]]
    )


def main_menu(language: str, role: str | None) -> InlineKeyboardMarkup:
    rows = [
        [button(tr(language, "btn_quests"), "browse:filter:all:0")],
        [button(tr(language, "btn_my_participations"), "quest:mylist:0")],
        [button(tr(language, "btn_ratings"), "ratings:overview")],
        [button(tr(language, "btn_guide"), "menu:guide"), button(tr(language, "btn_support"), "support:open")],
        [button(tr(language, "btn_language"), "menu:language")],
    ]
    if role in {"admin", "superadmin"}:
        rows.insert(1, [button(tr(language, "btn_my_quests"), "adminq:filter:all:0")])
        rows.insert(2, [button(tr(language, "btn_admin"), "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def home_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[button(tr(language, "btn_home"), "menu:home")]])


def admin_home_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[button(tr(language, "btn_admin_home"), "admin:home")]])


def ratings_overview_keyboard(language: str, role: str | None) -> InlineKeyboardMarkup:
    rows = [
        [
            button(tr(language, "period_week"), "ratings:period:week"),
            button(tr(language, "period_month"), "ratings:period:month"),
        ],
        [button(tr(language, "period_year"), "ratings:period:year")],
        [button(tr(language, "btn_public_quest_ratings"), "ratings:list:all:0")],
    ]
    if role in {"admin", "superadmin"}:
        rows.append([button(tr(language, "btn_managed_ratings"), "ratings:list:managed:0")])
    if role in {"admin", "superadmin"}:
        rows.append([button(tr(language, "btn_admin_home"), "admin:home")])
    else:
        rows.append([button(tr(language, "btn_home"), "menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def browse_filters(language: str, current: str, page: int, items: list[dict], page_size: int) -> InlineKeyboardMarkup:
    rows = [
        [
            button(tr(language, f"filter_{status}"), f"browse:filter:{status}:0")
            for status in ("all", "scheduled", "active", "completed")
        ],
        [button(tr(language, "filter_archived"), "browse:filter:archived:0")],
    ]
    rows.extend([[button(f"🧭 {item['title'][:47]}", f"quest:view:{item['id']}")] for item in items])
    nav = []
    if page > 0:
        nav.append(button("⬅️", f"browse:filter:{current}:{page - 1}"))
    if len(items) == page_size:
        nav.append(button("➡️", f"browse:filter:{current}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([button(tr(language, "btn_home"), "menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def quest_detail(
    language: str,
    quest: dict,
    joined: bool = False,
    from_my_quests: bool = False,
    my_quests_visibility: str = "all",
    my_quests_page: int = 0,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if (not joined and not quest.get("paused_at") and quest["visibility"] == "public"
            and quest["status"] in {"scheduled", "active"}):
        rows.append([button(tr(language, "btn_join"), f"quest:join:{quest['id']}")])
    rows.append([button(tr(language, "btn_rank"), f"rating:show:{quest['id']}")])
    if joined and quest.get("chat_id") and quest["status"] == "active" and not quest.get("paused_at"):
        rows.append([button(tr(language, "btn_invite"), f"quest:chatinvite:{quest['id']}")])
    back_data = (
        f"quest:mylist:{my_quests_visibility}:{max(0, my_quests_page)}"
        if from_my_quests
        else "browse:filter:all:0"
    )
    rows.append([button(tr(language, "btn_back"), back_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def join_confirmation_keyboard(language: str, quest_id: int, token: str | None = None) -> InlineKeyboardMarkup:
    suffix = f":{token}" if token else ""
    return InlineKeyboardMarkup(inline_keyboard=[[
        button(tr(language, "btn_confirm_join"), f"quest:joinconfirm:{quest_id}{suffix}"),
        button(tr(language, "btn_cancel"), f"quest:joincancel:{quest_id}"),
    ]])


def participating_quests_keyboard(
    language: str,
    quests: list[dict],
    page: int = 0,
    page_size: int = 20,
    visibility: str = "all",
) -> InlineKeyboardMarkup:
    if visibility not in {"all", "public", "private"}:
        visibility = "all"
    filter_labels = {
        "all": tr(language, "filter_all"),
        "public": tr(language, "visibility_public"),
        "private": tr(language, "visibility_private"),
    }
    rows = [[
        button(
            f"✓ {label}" if selected == visibility else label,
            f"quest:mylist:{selected}:0",
        )
        for selected, label in filter_labels.items()
    ]]
    rows.extend(
        [
            button(
                f"🧭 {quest['title'][:47]}",
                f"quest:view:my:{visibility}:{page}:{quest['id']}",
            )
        ]
        for quest in quests
    )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(button("◀️", f"quest:mylist:{visibility}:{page - 1}"))
    if len(quests) == page_size:
        nav.append(button("▶️", f"quest:mylist:{visibility}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([button(tr(language, "btn_home"), "menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def creation_cover_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button(tr(language, "btn_skip_photo"), "create:cover:skip")],
        [button(tr(language, "btn_cancel"), "create:cancel")],
    ])


def creation_visibility(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button(tr(language, "visibility_public"), "create:visibility:public")],
        [button(tr(language, "visibility_private"), "create:visibility:private")],
        [button(tr(language, "btn_cancel"), "create:cancel")],
    ])


def creation_progression(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button(tr(language, "progression_immediate"), "create:progression:immediate")],
        [button(tr(language, "progression_scheduled"), "create:progression:scheduled")],
        [button(tr(language, "btn_cancel"), "create:cancel")],
    ])


def creation_answer_mode(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button(tr(language, "answer_auto"), "create:answer:auto")],
        [button(tr(language, "answer_manual"), "create:answer:manual")],
        [button(tr(language, "btn_cancel"), "create:cancel")],
    ])


def creation_chat(language: str, chats: list[dict]) -> InlineKeyboardMarkup:
    rows = [[button(f"🤖 {tr(language, 'chat_private_only')}", "create:chat:0")]]
    rows.extend([[button(f"📣 {chat['title'][:45]}", f"create:chat:{chat['chat_id']}")] for chat in chats])
    rows.append([button(tr(language, "btn_cancel"), "create:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_panel(language: str, role: str) -> InlineKeyboardMarkup:
    rows = [
        [button(tr(language, "btn_create"), "create:start")],
        [button(tr(language, "btn_my_quests"), "adminq:filter:all:0")],
        [button(tr(language, "btn_ratings"), "ratings:list:managed:0")],
    ]
    if role == "superadmin":
        rows.extend([
            [button(tr(language, "btn_admins"), "super:admins")],
            [button(tr(language, "btn_stats"), "super:stats"), button(tr(language, "btn_logs"), "super:logs")],
            [button(tr(language, "btn_settings"), "super:settings")],
            [button(tr(language, "btn_export"), "super:export")],
        ])
    rows.append([button(tr(language, "btn_home"), "menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_quest_filters(language: str, current: str, page: int, items: list[dict], page_size: int) -> InlineKeyboardMarkup:
    rows = [[
        button(tr(language, f"filter_{status}"), f"adminq:filter:{status}:0")
        for status in ("all", "scheduled", "active", "completed")
    ], [button(tr(language, "filter_archived"), "adminq:filter:archived:0")]]
    rows.extend([[button(f"🛠 {item['title'][:45]}", f"manage:quest:{item['id']}")] for item in items])
    nav = []
    if page > 0:
        nav.append(button("⬅️", f"adminq:filter:{current}:{page - 1}"))
    if len(items) == page_size:
        nav.append(button("➡️", f"adminq:filter:{current}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([button(tr(language, "btn_admin_home"), "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manage_quest(language: str, quest: dict, role: str) -> InlineKeyboardMarkup:
    rows = [
        [button(tr(language, "btn_rank"), f"rating:show:{quest['id']}")],
        [button(tr(language, "btn_participants"), f"manage:participants:{quest['id']}")],
        [button(tr(language, "btn_pending"), f"manage:pending:{quest['id']}")],
    ]
    if quest["status"] != "archived":
        rows.append([button(tr(language, "btn_edit_details"), f"manage:editdetails:{quest['id']}")])
    if quest["status"] in {"scheduled", "active"}:
        rows.append([button(tr(language, "btn_edit_questions"), f"manage:editquestions:{quest['id']}")])
    if quest.get("visibility") == "private":
        rows.append([button(tr(language, "btn_private_link"), f"manage:privateinvite:{quest['id']}")])
    if quest["status"] in {"scheduled", "active"}:
        if quest.get("paused_at"):
            rows.append([button(tr(language, "btn_resume"), f"manage:resume:{quest['id']}")])
        else:
            rows.append([button(tr(language, "btn_pause"), f"manage:pause:{quest['id']}")])
        rows.append([button(tr(language, "btn_finish"), f"manage:finish-confirm:{quest['id']}")])
    if role == "superadmin":
        if quest["status"] == "archived":
            rows.append([button(tr(language, "btn_unarchive"), f"manage:unarchive:{quest['id']}")])
        else:
            rows.append([button(tr(language, "btn_archive"), f"manage:archive:{quest['id']}")])
    rows.append([button(tr(language, "btn_back"), "adminq:filter:all:0")])
    rows.append([button(tr(language, "btn_admin_home"), "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_quest_details_keyboard(language: str, quest_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button(tr(language, "btn_edit_title"), f"manage:edit:title:{quest_id}")],
        [button(tr(language, "btn_edit_description"), f"manage:edit:description:{quest_id}")],
        [button(tr(language, "btn_edit_cover"), f"manage:edit:cover:{quest_id}")],
        [button(tr(language, "btn_back"), f"manage:quest:{quest_id}")],
        [button(tr(language, "btn_admin_home"), "admin:home")],
    ])


def edit_quest_cover_keyboard(
    language: str, quest_id: int, has_cover: bool
) -> InlineKeyboardMarkup:
    rows = [[button(tr(language, "btn_replace_cover"), f"manage:edit:cover:photo:{quest_id}")]]
    if has_cover:
        rows.append([
            button(
                tr(language, "btn_remove_cover"),
                f"manage:edit:cover:remove:{quest_id}",
                style="danger",
            )
        ])
    rows.extend([
        [button(tr(language, "btn_back"), f"manage:editdetails:{quest_id}")],
        [button(tr(language, "btn_admin_home"), "admin:home")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_cover_removal_keyboard(language: str, quest_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        button(
            tr(language, "btn_confirm_remove_cover"),
            f"manage:edit:cover:remove-confirm:{quest_id}",
            style="danger",
        ),
        button(
            tr(language, "btn_cancel"),
            f"manage:edit:cover:{quest_id}",
            style="primary",
        ),
    ], [button(tr(language, "btn_admin_home"), "admin:home")]])


def editable_stages_keyboard(language: str, quest_id: int, stages: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [button(tr(language, "stage_label", number=stage["stage_order"]), f"manage:editstage:{quest_id}:{stage['stage_order']}")]
        for stage in stages
    ]
    rows.append([button(tr(language, "btn_back"), f"manage:quest:{quest_id}")])
    rows.append([button(tr(language, "btn_admin_home"), "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_stage_actions_keyboard(
    language: str, quest_id: int, stage_order: int, can_edit_answer: bool
) -> InlineKeyboardMarkup:
    rows = [[button(tr(language, "btn_edit_question"), f"manage:editquestion:{quest_id}:{stage_order}")]]
    if can_edit_answer:
        rows.append([button(tr(language, "btn_edit_answer"), f"manage:editanswer:{quest_id}:{stage_order}")])
    rows.append([button(tr(language, "btn_back"), f"manage:editquestions:{quest_id}")])
    rows.append([button(tr(language, "btn_admin_home"), "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_prompt_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button(tr(language, "btn_cancel"), "manage:editcancel")],
        [button(tr(language, "btn_admin_home"), "admin:home")],
    ])


def edit_done_keyboard(language: str, quest_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button(tr(language, "btn_edit_questions"), f"manage:editquestions:{quest_id}")],
            [button(tr(language, "btn_back"), f"manage:quest:{quest_id}")],
            [button(tr(language, "btn_admin_home"), "admin:home")],
        ]
    )


def participants_keyboard(
    language: str, quest_id: int, participants: list[dict], page: int = 0, page_size: int = 20
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    start = max(0, page) * page_size
    visible = participants[start : start + page_size]
    for participant in visible:
        label = participant.get("full_name") or participant.get("username") or str(participant["user_id"])
        action = "unban" if participant["status"] == "blocked" else "ban"
        status_key = {
            "joined": "participant_joined",
            "active": "participant_active",
            "completed": "participant_completed",
            "failed": "participant_failed",
            "blocked": "participant_blocked",
        }.get(participant["status"], "participant_active")
        rows.append([button(f"👤 {label[:23]} · {tr(language, status_key)}", f"manage:participant:{quest_id}:{participant['user_id']}:{action}")])
    nav = []
    if page > 0:
        nav.append(button("⬅️", f"manage:participants:{quest_id}:{page - 1}"))
    if start + page_size < len(participants):
        nav.append(button("➡️", f"manage:participants:{quest_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([button(tr(language, "btn_back"), f"manage:quest:{quest_id}")])
    rows.append([button(tr(language, "btn_admin_home"), "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def answer_quest_selector(language: str, quests: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [button(f"🧩 {item['title'][:30]} · {item['stage_order']}", f"answer:select:{item['quest_id']}")]
        for item in quests
    ]
    rows.append([button(tr(language, "btn_home"), "menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def review_keyboard(language: str, answer_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            button(tr(language, "btn_approve"), f"review:{answer_id}:yes"),
            button(tr(language, "btn_reject"), f"review:{answer_id}:no"),
        ],
        [button(tr(language, "btn_admin_home"), "admin:home")],
    ])


def admin_list_keyboard(language: str, admins: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for admin in admins:
        name = admin.get("full_name") or admin.get("username") or str(admin["telegram_id"])
        role_icon = "⭐" if admin["role"] == "superadmin" else "👤"
        role_label = tr(language, f"role_{admin['role']}")
        if admin["role"] == "admin":
            rows.append([button(f"{role_icon} {role_label} · {name[:20]} · {admin['telegram_id']}", f"super:removeadmin:{admin['telegram_id']}")])
        else:
            rows.append([button(f"{role_icon} {role_label} · {name[:20]} · {admin['telegram_id']}", "super:noop")])
    rows.append([button(tr(language, "btn_add"), "super:addadmin")])
    rows.append([button(tr(language, "btn_admin_home"), "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_keyboard(language: str, page_size: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button(tr(language, "btn_chats"), "super:chats")],
        [button(tr(language, "choose_page_size"), "super:pagesize")],
        [button(tr(language, "btn_admin_home"), "admin:home")],
    ])


def page_sizes_keyboard(language: str, current: int) -> InlineKeyboardMarkup:
    sizes = (5, 10, 20, 50)
    rows = [[button(f"{'✅' if size == current else '🔢'} {size}", f"super:pagesize:{size}") for size in sizes]]
    rows.append([button(tr(language, "btn_back"), "super:settings")])
    rows.append([button(tr(language, "btn_admin_home"), "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def managed_chats_keyboard(language: str, chats: list[dict]) -> InlineKeyboardMarkup:
    rows = [[button(tr(language, "btn_add"), "super:addchat")]]
    rows.extend([[button(f"📣 {chat['title'][:25]} · {chat['chat_id']}", f"super:chat:{chat['chat_id']}")] for chat in chats])
    rows.append([button(tr(language, "btn_back"), "super:settings")])
    rows.append([button(tr(language, "btn_admin_home"), "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manage_chat_keyboard(language: str, chat: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button(tr(language, "btn_toggle_cleanup"), f"super:cleanup:{chat['chat_id']}")],
        [button(tr(language, "btn_whitelist"), f"super:whitelist:{chat['chat_id']}")],
        [button(tr(language, "btn_back"), "super:chats")],
        [button(tr(language, "btn_admin_home"), "admin:home")],
    ])


def whitelist_keyboard(language: str, chat_id: int, users: list[dict]) -> InlineKeyboardMarkup:
    rows = [[button(tr(language, "btn_add"), f"super:addwhite:{chat_id}")]]
    rows.extend([[button(f"🗑️ {item['user_id']} · {tr(language, 'btn_remove')}", f"super:delwhite:{chat_id}:{item['user_id']}")] for item in users])
    rows.append([button(tr(language, "btn_back"), f"super:chat:{chat_id}")])
    rows.append([button(tr(language, "btn_admin_home"), "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def support_start_keyboard(language: str, quests: list[dict]) -> InlineKeyboardMarkup:
    rows = [[button(tr(language, "btn_support_super"), "support:new:super")]]
    rows.extend([[button(f"💬 {tr(language, 'btn_support_quest')}: {quest['title'][:27]}", f"support:new:quest:{quest['id']}")] for quest in quests])
    rows.append([button(tr(language, "btn_my_tickets"), "support:tickets")])
    rows.append([button(tr(language, "btn_home"), "menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ticket_reply_keyboard(language: str, ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        button(tr(language, "btn_reply_ticket"), f"support:reply:{ticket_id}")
    ]])
