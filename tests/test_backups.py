from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quest_bot.backups import _postgres_connection_details, database_backup


class DatabaseBackupTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_backup_is_restorable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "source.sqlite3"
            connection = sqlite3.connect(database_path)
            connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
            connection.execute("INSERT INTO sample(value) VALUES (?)", ("preserved",))
            connection.commit()
            connection.close()

            async with database_backup(str(database_path)) as backup_path:
                backup = sqlite3.connect(backup_path)
                row = backup.execute("SELECT value FROM sample").fetchone()
                backup.close()
                self.assertEqual(row, ("preserved",))

    async def test_postgres_backup_uses_private_passfile_and_streamable_dump(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_pg_dump = Path(directory) / "pg_dump"
            fake_pg_dump.write_text(
                "#!/usr/bin/env python3\n"
                "import os, pathlib, stat, sys\n"
                "passfile = pathlib.Path(os.environ['PGPASSFILE'])\n"
                "assert stat.S_IMODE(passfile.stat().st_mode) == 0o600\n"
                "assert '--no-password' in sys.argv\n"
                "output = next(arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--file='))\n"
                "pathlib.Path(output).write_bytes(b'custom-format-backup')\n",
                encoding="utf-8",
            )
            fake_pg_dump.chmod(0o755)
            original_path = os.environ.get("PATH", "/usr/bin:/bin")
            with patch.dict(os.environ, {"PATH": f"{directory}:{original_path}"}):
                async with database_backup(
                    "postgresql://quest:secret@db.example:5432/quest_bot"
                ) as backup_path:
                    self.assertEqual(backup_path.suffix, ".dump")
                    self.assertEqual(backup_path.read_bytes(), b"custom-format-backup")

    async def test_postgres_connection_options_keep_password_out_of_environment(self) -> None:
        dsn = (
            "postgresql://bot%40example:p%3Aa%5Css@db.example:5433/quest%20db"
            "?sslmode=require&connect_timeout=12"
        )
        with patch.dict(
            os.environ,
            {
                "BOT_TOKEN": "not-for-pg-dump",
                "DATABASE_URL": "postgresql://bot:secret@db.example/quest-db",
                "PGPASSWORD": "inherited-password",
            },
        ):
            environment, passfile_record = _postgres_connection_details(dsn)
        self.assertEqual(environment["PGHOST"], "db.example")
        self.assertEqual(environment["PGPORT"], "5433")
        self.assertEqual(environment["PGUSER"], "bot@example")
        self.assertEqual(environment["PGDATABASE"], "quest db")
        self.assertEqual(environment["PGSSLMODE"], "require")
        self.assertEqual(environment["PGCONNECT_TIMEOUT"], "12")
        self.assertNotIn("PGPASSWORD", environment)
        self.assertNotIn("BOT_TOKEN", environment)
        self.assertNotIn("DATABASE_URL", environment)
        self.assertEqual(
            passfile_record,
            ("db.example", "5433", "quest db", "bot@example", "p\\:a\\\\ss"),
        )


if __name__ == "__main__":
    unittest.main()
