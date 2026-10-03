from __future__ import annotations

import unittest
from unittest.mock import patch

from quest_bot.config import Settings
from quest_bot.database import QUEST_LEADERBOARD_QUERY, SCHEMA, Database
from quest_bot.postgres import (
    AsyncpgConnection,
    AsyncpgCursor,
    postgres_schema,
    translate_sql,
)


class PostgresCompatibilityTests(unittest.TestCase):
    def test_placeholders_ignore_quoted_question_marks(self) -> None:
        sql = translate_sql('SELECT ?, \'?\' AS literal, "?" AS identifier')
        self.assertEqual(sql, 'SELECT $1, \'?\' AS literal, "?" AS identifier')

    def test_insert_or_ignore_and_nocase_are_translated(self) -> None:
        ignored = translate_sql(
            "INSERT OR IGNORE INTO users(telegram_id,full_name) VALUES(?,?)"
        )
        self.assertEqual(
            ignored,
            "INSERT INTO users(telegram_id,full_name) VALUES($1,$2) ON CONFLICT DO NOTHING",
        )
        self.assertEqual(
            translate_sql("SELECT * FROM managed_chats ORDER BY title COLLATE NOCASE"),
            "SELECT * FROM managed_chats ORDER BY lower(title)",
        )

    def test_per_quest_leaderboard_groups_every_selected_nonaggregate_column(self) -> None:
        group_by = QUEST_LEADERBOARD_QUERY.split("GROUP BY", 1)[1].split("ORDER BY", 1)[0]
        for column in (
            "p.quest_id",
            "p.user_id",
            "p.status",
            "p.joined_at",
            "p.completed_at",
            "u.full_name",
            "u.username",
        ):
            self.assertIn(column, group_by)

    def test_schema_uses_postgresql_identity_and_bigint_types(self) -> None:
        schema = postgres_schema(SCHEMA)
        self.assertIn("id BIGSERIAL PRIMARY KEY", schema)
        self.assertIn("telegram_id BIGINT PRIMARY KEY", schema)
        self.assertIn("source_chat_id BIGINT", schema)
        self.assertIn("source_message_id BIGINT", schema)
        self.assertIn("cover_chat_id BIGINT", schema)
        self.assertIn("cover_message_id BIGINT", schema)
        self.assertIn("paused_at TEXT", schema)
        self.assertNotIn("AUTOINCREMENT", schema)
        self.assertNotIn("INTEGER", schema)

    def test_postgres_urls_are_normalized(self) -> None:
        database = Database("postgres://bot:secret@localhost/quests")
        self.assertTrue(database.is_postgres)
        self.assertEqual(database.dsn, "postgresql://bot:secret@localhost/quests")

        asyncpg_database = Database("postgresql+asyncpg://bot:secret@localhost/quests")
        self.assertEqual(asyncpg_database.dsn, "postgresql://bot:secret@localhost/quests")

    def test_settings_load_postgres_url_and_pool_sizes(self) -> None:
        values = {
            "BOT_TOKEN": "123456:abcdefghijklmnopqrstuvwxyzABCDEFG12345",
            "SUPERADMIN_IDS": "123,456",
            "QUESTION_ARCHIVE_CHANNEL_ID": "-1001234567890",
            "DATABASE_URL": "postgresql://bot:secret@localhost/quests",
            "DATABASE_POOL_MIN_SIZE": "2",
            "DATABASE_POOL_MAX_SIZE": "12",
            "SCHEDULER_INTERVAL_SECONDS": "7",
            "BACKUP_UPLOAD_TIMEOUT_SECONDS": "1200",
            "SERVICE_STOP_TIMEOUT_SECONDS": "2400",
        }
        with patch.dict("os.environ", values):
            settings = Settings.from_env()
        self.assertEqual(settings.database_dsn, values["DATABASE_URL"])
        self.assertEqual(settings.question_archive_channel_id, -1001234567890)
        self.assertEqual(settings.database_pool_min_size, 2)
        self.assertEqual(settings.database_pool_max_size, 12)
        self.assertEqual(settings.scheduler_interval_seconds, 7)
        self.assertEqual(settings.backup_upload_timeout_seconds, 1200)
        self.assertEqual(settings.service_stop_timeout_seconds, 2400)

    def test_settings_require_a_question_archive_channel_id(self) -> None:
        values = {
            "BOT_TOKEN": "123456:abcdefghijklmnopqrstuvwxyzABCDEFG12345",
            "SUPERADMIN_IDS": "123",
            "QUESTION_ARCHIVE_CHANNEL_ID": "",
        }
        with (
            patch.dict("os.environ", values, clear=True),
            self.assertRaisesRegex(ValueError, "QUESTION_ARCHIVE_CHANNEL_ID"),
        ):
            Settings.from_env()

    def test_cursor_description_uses_column_names(self) -> None:
        class RecordLike:
            def items(self):
                return iter((("id", 7), ("title", "Example")))

            def __iter__(self):
                return iter((7, "Example"))

        cursor = AsyncpgCursor([RecordLike()], rowcount=1)  # type: ignore[list-item]
        self.assertEqual([column[0] for column in cursor.description], ["id", "title"])


class PostgresTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_transaction_is_closed_only_once(self) -> None:
        class FakeTransaction:
            def __init__(self) -> None:
                self.commits = 0
                self.rollbacks = 0

            async def commit(self) -> None:
                self.commits += 1

            async def rollback(self) -> None:
                self.rollbacks += 1

        class FakeConnection:
            pass

        transaction = FakeTransaction()
        connection = AsyncpgConnection(FakeConnection(), transaction)  # type: ignore[arg-type]
        await connection.commit()
        await connection.commit()
        await connection.rollback()
        self.assertEqual(transaction.commits, 1)
        self.assertEqual(transaction.rollbacks, 0)


if __name__ == "__main__":
    unittest.main()
