from __future__ import annotations

import unittest
from datetime import datetime, timezone

from aiogram.types import Chat, ErrorEvent, Message, Update, User

from quest_bot.config import Settings
from quest_bot.error_reporting import UpdateLoggingMiddleware, handle_update_error


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


class FakeDatabase:
    async def get_language(self, user_id: int) -> str:
        return "en"


class ErrorReportingTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_logger_records_identifiers_without_message_content(
        self,
    ) -> None:
        message = Message(
            message_id=3,
            date=datetime.now(timezone.utc),
            chat=Chat(id=41, type="private"),
            from_user=User(id=41, is_bot=False, first_name="Test"),
            text="private user content",
        )
        update = Update(update_id=17, message=message)

        async def next_handler(event, data):
            return "handled"

        middleware = UpdateLoggingMiddleware()
        with self.assertLogs("quest_bot.error_reporting", level="INFO") as captured:
            result = await middleware(next_handler, update, {"event_update": update})

        self.assertEqual(result, "handled")
        record = "\\n".join(captured.output)
        self.assertIn("update_id=17", record)
        self.assertIn("user_id=41", record)
        self.assertNotIn("private user content", record)

    async def test_unhandled_private_update_notifies_user_and_redacted_superadmin_report(
        self,
    ) -> None:
        token = "123456:abcdefghijklmnopqrstuvwxyzABCDEFG12345"
        dsn = "postgresql://quest_bot:db-password@127.0.0.1:5432/quest_bot"
        settings = Settings(
            bot_token=token,
            superadmin_ids=(900,),
            database_dsn=dsn,
        )
        message = Message(
            message_id=3,
            date=datetime.now(timezone.utc),
            chat=Chat(id=41, type="private"),
            from_user=User(id=41, is_bot=False, first_name="Test"),
            text="private user content",
        )
        event = ErrorEvent(
            update=Update(update_id=17, message=message),
            exception=RuntimeError(f"failure with {token} and db-password"),
        )
        bot = FakeBot()

        with self.assertLogs("quest_bot.error_reporting", level="ERROR"):
            handled = await handle_update_error(event, bot, FakeDatabase(), settings)

        self.assertTrue(handled)
        self.assertEqual(bot.messages[0][0], 41)
        self.assertIn("Something went wrong", bot.messages[0][1])
        self.assertEqual(bot.messages[1][0], 900)
        report = bot.messages[1][1]
        self.assertIn("Update ID: 17", report)
        self.assertIn("User ID: 41", report)
        self.assertIn("RuntimeError", report)
        self.assertNotIn(token, report)
        self.assertNotIn("db-password", report)
        self.assertNotIn("private user content", report)


if __name__ == "__main__":
    unittest.main()
