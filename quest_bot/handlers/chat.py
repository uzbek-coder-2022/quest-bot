"""Group/channel membership tracking for optional start-of-quest cleanup."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import ChatMemberUpdated

from ..database import Database
from ..services import is_active_chat_member

router = Router(name="chat_membership")


@router.chat_member()
async def track_chat_member(update: ChatMemberUpdated, db: Database) -> None:
    chat_id = update.chat.id
    if not await db.get_managed_chat(chat_id):
        return
    member = update.new_chat_member
    user = member.user
    if user.is_bot:
        return
    active = is_active_chat_member(member.status, getattr(member, "is_member", None))
    full_name = " ".join(part for part in (user.first_name, user.last_name) if part)
    await db.track_chat_member(chat_id, user.id, full_name, user.username, active)
    invite_link = update.invite_link
    if active and invite_link:
        await db.mark_chat_invite_used(chat_id, invite_link.invite_link)
