"""Telegram command visibility scopes for public users and administrators."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

logger = logging.getLogger(__name__)

PUBLIC_COMMANDS = (
    BotCommand(command="start", description="Open the bot / Bosh menyu"),
    BotCommand(command="menu", description="Main menu"),
    BotCommand(command="quests", description="Browse public quests"),
    BotCommand(command="support", description="Contact an administrator"),
    BotCommand(command="language", description="Change interface language"),
    BotCommand(command="help", description="Usage guide"),
    BotCommand(command="cancel", description="Cancel current action"),
)

GROUP_ADMIN_COMMANDS = (
    *PUBLIC_COMMANDS,
    BotCommand(command="chatid", description="Show this group or channel ID"),
)

APP_ADMIN_COMMANDS = (
    *PUBLIC_COMMANDS,
    BotCommand(command="admin", description="Admin panel"),
    BotCommand(command="chatid", description="Show this group or channel ID"),
)


def _private_admin_scope(user_id: int) -> BotCommandScopeChat:
    """Return a user-specific command scope for an administrator's private chat."""
    return BotCommandScopeChat(chat_id=user_id)


async def set_app_admin_commands(bot: Bot, user_id: int) -> None:
    """Show the full command list in one application administrator's private chat."""
    try:
        await bot.set_my_commands(
            list(APP_ADMIN_COMMANDS), scope=_private_admin_scope(user_id)
        )
        logger.info("Set private administrator command scope for user %s", user_id)
    except TelegramAPIError:
        logger.exception(
            "Could not set administrator commands for Telegram user %s", user_id
        )


async def clear_app_admin_commands(bot: Bot, user_id: int) -> None:
    """Remove a user's private override so they fall back to public commands."""
    try:
        await bot.delete_my_commands(scope=_private_admin_scope(user_id))
        logger.info("Cleared private administrator command scope for user %s", user_id)
    except TelegramAPIError:
        logger.exception(
            "Could not clear administrator commands for Telegram user %s", user_id
        )


async def configure_bot_commands(bot: Bot, admin_ids: Iterable[int]) -> None:
    """Install public commands and admin-only command scopes."""
    await bot.set_my_commands(list(PUBLIC_COMMANDS), scope=BotCommandScopeDefault())
    await bot.set_my_commands(
        list(GROUP_ADMIN_COMMANDS),
        scope=BotCommandScopeAllChatAdministrators(),
    )
    configured_admin_ids = sorted({int(admin_id) for admin_id in admin_ids})
    for user_id in configured_admin_ids:
        await set_app_admin_commands(bot, user_id)
    logger.info(
        "Configured public and administrator command scopes for %s app admins",
        len(configured_admin_ids),
    )
