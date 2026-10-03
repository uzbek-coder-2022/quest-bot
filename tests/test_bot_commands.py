from __future__ import annotations

import unittest

from aiogram.types import (
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

from quest_bot.bot_commands import (
    APP_ADMIN_COMMANDS,
    GROUP_ADMIN_COMMANDS,
    PUBLIC_COMMANDS,
    clear_app_admin_commands,
    configure_bot_commands,
    set_app_admin_commands,
)


class FakeBot:
    def __init__(self) -> None:
        self.command_sets: list[tuple[list, object]] = []
        self.command_deletes: list[object] = []

    async def set_my_commands(self, commands: list, scope: object) -> None:
        self.command_sets.append((commands, scope))

    async def delete_my_commands(self, scope: object) -> None:
        self.command_deletes.append(scope)


class BotCommandScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_and_admin_commands_use_separate_scopes(self) -> None:
        bot = FakeBot()

        await configure_bot_commands(bot, [202, 101, 202])

        self.assertEqual(len(bot.command_sets), 4)
        public, public_scope = bot.command_sets[0]
        group_admin, group_scope = bot.command_sets[1]
        app_admins = bot.command_sets[2:]

        self.assertIsInstance(public_scope, BotCommandScopeDefault)
        self.assertNotIn("admin", {command.command for command in public})
        self.assertNotIn("chatid", {command.command for command in public})
        self.assertIsInstance(group_scope, BotCommandScopeAllChatAdministrators)
        self.assertIn("chatid", {command.command for command in group_admin})
        self.assertNotIn("admin", {command.command for command in group_admin})
        self.assertEqual(
            [scope.chat_id for _, scope in app_admins],
            [101, 202],
        )
        for commands, scope in app_admins:
            self.assertIsInstance(scope, BotCommandScopeChat)
            names = {command.command for command in commands}
            self.assertIn("admin", names)
            self.assertIn("chatid", names)
            self.assertTrue({command.command for command in PUBLIC_COMMANDS} <= names)
        self.assertEqual(
            {command.command for command in APP_ADMIN_COMMANDS},
            {command.command for command in app_admins[0][0]},
        )
        self.assertEqual(
            {command.command for command in GROUP_ADMIN_COMMANDS},
            {command.command for command in group_admin},
        )

    async def test_admin_scope_can_be_updated_or_cleared_immediately(self) -> None:
        bot = FakeBot()

        await set_app_admin_commands(bot, 303)
        await clear_app_admin_commands(bot, 303)

        self.assertEqual(bot.command_sets[0][1], BotCommandScopeChat(chat_id=303))
        self.assertEqual(bot.command_deletes, [BotCommandScopeChat(chat_id=303)])


if __name__ == "__main__":
    unittest.main()
