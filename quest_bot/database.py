"""Async persistence layer for quests, participants, moderation, and support."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import asyncpg

from .postgres import AsyncpgConnection, postgres_schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'uz' CHECK (language IN ('uz', 'ru', 'en')),
    is_banned INTEGER NOT NULL DEFAULT 0,
    ban_reason TEXT,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admins (
    telegram_id INTEGER PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('admin', 'superadmin')),
    added_by INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
    invite_token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'active', 'completed', 'archived')),
    progression TEXT NOT NULL CHECK (progression IN ('immediate', 'scheduled')),
    start_at TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    chat_id INTEGER,
    cleanup_done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quest_id INTEGER NOT NULL REFERENCES quests(id) ON DELETE CASCADE,
    stage_order INTEGER NOT NULL,
    question TEXT NOT NULL,
    answer_mode TEXT NOT NULL CHECK (answer_mode IN ('auto', 'manual')),
    correct_answer TEXT,
    max_attempts INTEGER NOT NULL CHECK (max_attempts BETWEEN 1 AND 100),
    time_limit_seconds INTEGER NOT NULL DEFAULT 0,
    starts_at TEXT NOT NULL,
    UNIQUE (quest_id, stage_order)
);

CREATE TABLE IF NOT EXISTS quest_participants (
    quest_id INTEGER NOT NULL REFERENCES quests(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('joined', 'active', 'completed', 'failed', 'blocked')),
    joined_at TEXT NOT NULL,
    current_stage INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    ban_reason TEXT,
    previous_status TEXT,
    PRIMARY KEY (quest_id, user_id)
);

CREATE TABLE IF NOT EXISTS participant_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quest_id INTEGER NOT NULL REFERENCES quests(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    stage_id INTEGER NOT NULL REFERENCES stages(id) ON DELETE CASCADE,
    stage_order INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'pending_review', 'correct', 'wrong', 'timeout', 'skipped')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (quest_id, user_id, stage_id)
);

CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quest_id INTEGER NOT NULL REFERENCES quests(id) ON DELETE CASCADE,
    stage_id INTEGER NOT NULL REFERENCES stages(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    answer_text TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('pending', 'correct', 'wrong')),
    created_at TEXT NOT NULL,
    reviewed_by INTEGER,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS managed_chats (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    chat_type TEXT NOT NULL,
    cleanup_enabled INTEGER NOT NULL DEFAULT 0,
    added_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_members (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    is_member INTEGER NOT NULL DEFAULT 1,
    joined_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS chat_whitelist (
    chat_id INTEGER NOT NULL REFERENCES managed_chats(chat_id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    added_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS chat_invites (
    quest_id INTEGER NOT NULL REFERENCES quests(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    chat_id INTEGER NOT NULL,
    invite_link TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at TEXT,
    PRIMARY KEY (quest_id, user_id)
);

CREATE TABLE IF NOT EXISTS announced_stages (
    quest_id INTEGER NOT NULL REFERENCES quests(id) ON DELETE CASCADE,
    stage_id INTEGER NOT NULL REFERENCES stages(id) ON DELETE CASCADE,
    announced_at TEXT NOT NULL,
    PRIMARY KEY (quest_id, stage_id)
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    quest_id INTEGER REFERENCES quests(id) ON DELETE SET NULL,
    target_admin_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS support_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
    sender_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quests_status_start ON quests(status, start_at);
CREATE INDEX IF NOT EXISTS idx_quests_owner ON quests(owner_id, status);
CREATE INDEX IF NOT EXISTS idx_participants_user ON quest_participants(user_id, status);
CREATE INDEX IF NOT EXISTS idx_stage_sessions_status ON participant_stages(status, started_at);
CREATE INDEX IF NOT EXISTS idx_answers_pending ON answers(quest_id, verdict, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_members_active ON chat_members(chat_id, is_member);
"""


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp for persistence in either backend."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_exact_answer(value: str) -> str:
    """Normalize Unicode and outer whitespace while preserving case and punctuation."""
    import unicodedata

    return unicodedata.normalize("NFC", value).strip()


class Database:
    """Async PostgreSQL repository with SQLite support for local development and tests."""

    def __init__(
        self,
        dsn: str | Path,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
    ) -> None:
        self.dsn = str(dsn)
        self.pool_min_size = pool_min_size
        self.pool_max_size = pool_max_size
        self.is_postgres = self.dsn.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://"))
        if self.is_postgres:
            self.dsn = self.dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
            if self.dsn.startswith("postgres://"):
                self.dsn = "postgresql://" + self.dsn.removeprefix("postgres://")
        elif "://" in self.dsn:
            raise ValueError("DATABASE_URL must use a PostgreSQL URL")
        self.path = self.dsn
        self._pool: asyncpg.Pool | None = None

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[Any]:
        if self.is_postgres:
            if self._pool is None:
                raise RuntimeError("Database.initialize() must be called before database operations")
            async with self._pool.acquire() as raw_connection:
                transaction = raw_connection.transaction()
                await transaction.start()
                connection = AsyncpgConnection(raw_connection, transaction)
                try:
                    yield connection
                except BaseException:
                    await connection.rollback()
                    raise
                else:
                    await connection.commit()
            return

        connection = await aiosqlite.connect(self.path, timeout=30)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            await connection.close()

    async def initialize(self) -> None:
        """Create database tables and initialize the selected backend."""
        if self.is_postgres:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    dsn=self.dsn,
                    min_size=self.pool_min_size,
                    max_size=self.pool_max_size,
                    command_timeout=60,
                )
            try:
                async with self._connection() as connection:
                    await connection.executescript(postgres_schema(SCHEMA))
            except BaseException:
                await self.close()
                raise
            return

        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with self._connection() as connection:
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.executescript(SCHEMA)
            await connection.commit()

    async def close(self) -> None:
        """Close the PostgreSQL connection pool, when one is active."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def seed_superadmins(self, user_ids: Iterable[int]) -> None:
        now = utc_now()
        configured_ids = tuple(dict.fromkeys(int(user_id) for user_id in user_ids))
        if not configured_ids:
            raise ValueError("At least one superadmin ID is required")
        placeholders = ",".join("?" for _ in configured_ids)
        async with self._connection() as connection:
            await connection.execute(
                f"DELETE FROM admins WHERE role='superadmin' AND telegram_id NOT IN ({placeholders})",
                configured_ids,
            )
            for user_id in configured_ids:
                await connection.execute(
                    "INSERT OR IGNORE INTO users(telegram_id, full_name, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
                    (user_id, "Superadmin", now, now),
                )
                await connection.execute(
                    "INSERT INTO admins(telegram_id, role, added_by, created_at) VALUES (?, 'superadmin', ?, ?) "
                    "ON CONFLICT(telegram_id) DO UPDATE SET role='superadmin'",
                    (user_id, user_id, now),
                )
            await connection.commit()

    async def ensure_user(self, user_id: int, username: str | None, full_name: str) -> None:
        now = utc_now()
        async with self._connection() as connection:
            await connection.execute(
                "INSERT INTO users(telegram_id, username, full_name, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username, "
                "full_name=excluded.full_name, last_seen_at=excluded.last_seen_at",
                (user_id, username, full_name[:160], now, now),
            )
            await connection.commit()

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT * FROM users WHERE telegram_id=?", (user_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_language(self, user_id: int) -> str:
        user = await self.get_user(user_id)
        return str(user["language"]) if user else "uz"

    async def set_language(self, user_id: int, language: str) -> None:
        if language not in {"uz", "ru", "en"}:
            raise ValueError("Unsupported language")
        async with self._connection() as connection:
            await connection.execute(
                "UPDATE users SET language=?, last_seen_at=? WHERE telegram_id=?",
                (language, utc_now(), user_id),
            )
            await connection.commit()

    async def get_role(self, user_id: int) -> str | None:
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT role FROM admins WHERE telegram_id=?", (user_id,))
            row = await cursor.fetchone()
            return str(row["role"]) if row else None

    async def list_admins(self) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT a.telegram_id, a.role, a.added_by, a.created_at, u.username, u.full_name "
                "FROM admins a LEFT JOIN users u ON u.telegram_id=a.telegram_id "
                "ORDER BY CASE a.role WHEN 'superadmin' THEN 0 ELSE 1 END, a.created_at"
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def add_admin(self, user_id: int, actor_id: int) -> bool:
        now = utc_now()
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute("SELECT role FROM admins WHERE telegram_id=?", (user_id,))
            existing = await cursor.fetchone()
            if existing:
                await connection.rollback()
                return False
            await connection.execute(
                "INSERT OR IGNORE INTO users(telegram_id, full_name, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
                (user_id, "", now, now),
            )
            await connection.execute(
                "INSERT INTO admins(telegram_id, role, added_by, created_at) VALUES (?, 'admin', ?, ?)",
                (user_id, actor_id, now),
            )
            await connection.commit()
            return True

    async def remove_admin(self, user_id: int) -> bool:
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT role FROM admins WHERE telegram_id=?", (user_id,))
            row = await cursor.fetchone()
            if not row or row["role"] == "superadmin":
                return False
            await connection.execute("DELETE FROM admins WHERE telegram_id=?", (user_id,))
            await connection.commit()
            return True

    async def is_globally_banned(self, user_id: int) -> tuple[bool, str | None]:
        user = await self.get_user(user_id)
        if not user:
            return False, None
        return bool(user["is_banned"]), user.get("ban_reason")

    async def set_global_ban(self, user_id: int, reason: str | None, banned: bool) -> None:
        now = utc_now()
        async with self._connection() as connection:
            await connection.execute(
                "UPDATE users SET is_banned=?, ban_reason=?, last_seen_at=? WHERE telegram_id=?",
                (int(banned), reason if banned else None, now, user_id),
            )
            await connection.commit()

    async def create_quest(self, owner_id: int, quest: dict[str, Any], stages: list[dict[str, Any]]) -> int:
        now = utc_now()
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "INSERT INTO quests(owner_id,title,description,visibility,invite_token,status,progression,start_at,"
                "duration_seconds,chat_id,created_at,updated_at) VALUES(?,?,?,?,?,'scheduled',?,?,?,?,?,?)",
                (
                    owner_id,
                    quest["title"],
                    quest.get("description", ""),
                    quest["visibility"],
                    quest["invite_token"],
                    quest["progression"],
                    quest["start_at"],
                    int(quest.get("duration_seconds", 0)),
                    quest.get("chat_id"),
                    now,
                    now,
                ),
            )
            quest_id = int(cursor.lastrowid)
            for index, stage in enumerate(stages, start=1):
                await connection.execute(
                    "INSERT INTO stages(quest_id,stage_order,question,answer_mode,correct_answer,max_attempts,"
                    "time_limit_seconds,starts_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        quest_id,
                        index,
                        stage["question"],
                        stage["answer_mode"],
                        stage.get("correct_answer"),
                        int(stage["max_attempts"]),
                        int(stage.get("time_limit_seconds", 0)),
                        stage["starts_at"],
                    ),
                )
            await connection.execute(
                "INSERT INTO audit_logs(actor_id,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
                (owner_id, "quest.created", "quest", str(quest_id), json.dumps({"title": quest["title"]}, ensure_ascii=False), now),
            )
            await connection.commit()
            return quest_id

    @staticmethod
    def _quest_dict(row: Any) -> dict[str, Any]:
        return dict(row)

    async def get_quest(self, quest_id: int) -> dict[str, Any] | None:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT q.*, (SELECT COUNT(*) FROM stages s WHERE s.quest_id=q.id) AS stage_count, "
                "(SELECT title FROM managed_chats c WHERE c.chat_id=q.chat_id) AS chat_title "
                "FROM quests q WHERE q.id=?",
                (quest_id,),
            )
            row = await cursor.fetchone()
            return self._quest_dict(row) if row else None

    async def get_quest_by_token(self, quest_id: int, token: str) -> dict[str, Any] | None:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT q.*, (SELECT COUNT(*) FROM stages s WHERE s.quest_id=q.id) AS stage_count, "
                "(SELECT title FROM managed_chats c WHERE c.chat_id=q.chat_id) AS chat_title "
                "FROM quests q WHERE q.id=? AND q.invite_token=?",
                (quest_id, token),
            )
            row = await cursor.fetchone()
            return self._quest_dict(row) if row else None

    async def list_public_quests(
        self, status: str | None, offset: int, limit: int
    ) -> list[dict[str, Any]]:
        allowed = {"scheduled", "active", "completed", "archived"}
        if status not in allowed | {None}:
            status = None
        async with self._connection() as connection:
            if status is None:
                cursor = await connection.execute(
                    "SELECT q.*, (SELECT COUNT(*) FROM stages s WHERE s.quest_id=q.id) AS stage_count "
                    "FROM quests q WHERE q.visibility='public' AND q.status!='archived' "
                    "ORDER BY CASE q.status WHEN 'active' THEN 0 WHEN 'scheduled' THEN 1 ELSE 2 END, q.start_at LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            else:
                cursor = await connection.execute(
                    "SELECT q.*, (SELECT COUNT(*) FROM stages s WHERE s.quest_id=q.id) AS stage_count "
                    "FROM quests q WHERE q.visibility='public' AND q.status=? ORDER BY q.start_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                )
            return [dict(row) for row in await cursor.fetchall()]

    async def list_manageable_quests(
        self, requester_id: int, is_superadmin: bool, status: str | None, offset: int, limit: int
    ) -> list[dict[str, Any]]:
        valid_statuses = {"scheduled", "active", "completed", "archived"}
        clauses: list[str] = []
        values: list[Any] = []
        if not is_superadmin:
            clauses.append("q.owner_id=?")
            values.append(requester_id)
        if status in valid_statuses:
            clauses.append("q.status=?")
            values.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = (
            "SELECT q.*, (SELECT COUNT(*) FROM stages s WHERE s.quest_id=q.id) AS stage_count "
            "FROM quests q" + where + " ORDER BY q.created_at DESC LIMIT ? OFFSET ?"
        )
        values.extend([limit, offset])
        async with self._connection() as connection:
            cursor = await connection.execute(query, values)
            return [dict(row) for row in await cursor.fetchall()]

    async def list_quest_stages(self, quest_id: int) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM stages WHERE quest_id=? ORDER BY stage_order", (quest_id,)
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def get_stage(self, quest_id: int, stage_order: int) -> dict[str, Any] | None:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM stages WHERE quest_id=? AND stage_order=?", (quest_id, stage_order)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def list_scheduled_quests_due(self, now: str) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM quests WHERE status='scheduled' AND start_at<=? ORDER BY start_at", (now,)
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def list_active_quests(self) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT * FROM quests WHERE status='active' ORDER BY id")
            return [dict(row) for row in await cursor.fetchall()]

    async def mark_quest_active(self, quest_id: int) -> bool:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "UPDATE quests SET status='active', updated_at=? WHERE id=? AND status='scheduled'",
                (utc_now(), quest_id),
            )
            await connection.commit()
            return cursor.rowcount == 1

    async def set_quest_status(self, quest_id: int, status: str) -> list[int]:
        if status not in {"scheduled", "active", "completed", "archived"}:
            raise ValueError("Invalid quest status")
        now = utc_now()
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            await connection.execute(
                "UPDATE quests SET status=?, updated_at=?, completed_at=CASE WHEN ?='completed' THEN ? ELSE completed_at END "
                "WHERE id=?",
                (status, now, status, now, quest_id),
            )
            cursor = await connection.execute(
                "SELECT user_id FROM quest_participants WHERE quest_id=? AND status IN ('joined','active')",
                (quest_id,),
            )
            user_ids = [int(row["user_id"]) for row in await cursor.fetchall()]
            await connection.execute(
                "INSERT INTO audit_logs(actor_id,action,entity_type,entity_id,details,created_at) VALUES(NULL,?,?,?,'{}',?)",
                (f"quest.{status}", "quest", str(quest_id), now),
            )
            await connection.commit()
            return user_ids

    async def claim_cleanup(self, quest_id: int) -> bool:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "UPDATE quests SET cleanup_done=1 WHERE id=? AND cleanup_done=0", (quest_id,)
            )
            await connection.commit()
            return cursor.rowcount == 1

    async def get_latest_due_stage(self, quest_id: int, now: str) -> dict[str, Any] | None:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM stages WHERE quest_id=? AND starts_at<=? ORDER BY stage_order DESC LIMIT 1",
                (quest_id, now),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def claim_stage_announcement(self, quest_id: int, stage_id: int) -> bool:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "INSERT OR IGNORE INTO announced_stages(quest_id,stage_id,announced_at) VALUES(?,?,?)",
                (quest_id, stage_id, utc_now()),
            )
            await connection.commit()
            return cursor.rowcount == 1

    async def join_quest(
        self, quest_id: int, user_id: int, private_token: str | None, now: str
    ) -> dict[str, Any]:
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute("SELECT * FROM quests WHERE id=?", (quest_id,))
            quest_row = await cursor.fetchone()
            if not quest_row:
                await connection.rollback()
                return {"code": "not_found"}
            quest = dict(quest_row)
            if quest["visibility"] == "private" and private_token != quest["invite_token"]:
                await connection.rollback()
                return {"code": "invalid_token", "quest": quest}
            if quest["status"] not in {"scheduled", "active"}:
                await connection.rollback()
                return {"code": "closed", "quest": quest}
            cursor = await connection.execute("SELECT is_banned, ban_reason FROM users WHERE telegram_id=?", (user_id,))
            user_row = await cursor.fetchone()
            if user_row and user_row["is_banned"]:
                await connection.rollback()
                return {"code": "globally_banned", "reason": user_row["ban_reason"], "quest": quest}
            cursor = await connection.execute(
                "SELECT status FROM quest_participants WHERE quest_id=? AND user_id=?", (quest_id, user_id)
            )
            existing = await cursor.fetchone()
            if existing:
                await connection.rollback()
                return {"code": "blocked" if existing["status"] == "blocked" else "already_joined", "quest": quest}
            await connection.execute(
                "INSERT INTO quest_participants(quest_id,user_id,status,joined_at,current_stage) VALUES(?,?,?,?,0)",
                (quest_id, user_id, "joined" if quest["status"] == "scheduled" else "active", now),
            )
            await connection.commit()
            return {"code": "joined", "quest": quest}

    async def participant(self, quest_id: int, user_id: int) -> dict[str, Any] | None:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM quest_participants WHERE quest_id=? AND user_id=?", (quest_id, user_id)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def activate_stage_for_participant(
        self, quest_id: int, user_id: int, stage: dict[str, Any], now: str
    ) -> bool:
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "SELECT p.status,p.current_stage,q.status AS quest_status FROM quest_participants p "
                "JOIN quests q ON q.id=p.quest_id WHERE p.quest_id=? AND p.user_id=?",
                (quest_id, user_id),
            )
            participant = await cursor.fetchone()
            if not participant or participant["quest_status"] != "active" or participant["status"] not in {"joined", "active"} or int(participant["current_stage"]) >= int(stage["stage_order"]):
                await connection.rollback()
                return False
            await connection.execute(
                "UPDATE participant_stages SET status='skipped', completed_at=? "
                "WHERE quest_id=? AND user_id=? AND stage_order<? AND status='open'",
                (now, quest_id, user_id, int(stage["stage_order"])),
            )
            cursor = await connection.execute(
                "INSERT OR IGNORE INTO participant_stages(quest_id,user_id,stage_id,stage_order,status,started_at) "
                "VALUES(?,?,?,?, 'open', ?)",
                (quest_id, user_id, stage["id"], stage["stage_order"], now),
            )
            if cursor.rowcount != 1:
                await connection.rollback()
                return False
            await connection.execute(
                "UPDATE quest_participants SET status='active', current_stage=? WHERE quest_id=? AND user_id=?",
                (stage["stage_order"], quest_id, user_id),
            )
            await connection.commit()
            return True

    async def activate_stage_for_quest(
        self, quest_id: int, stage: dict[str, Any], now: str
    ) -> list[int]:
        activated: list[int] = []
        order = int(stage["stage_order"])
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "SELECT p.user_id,p.current_stage FROM quest_participants p JOIN quests q ON q.id=p.quest_id "
                "WHERE p.quest_id=? AND q.status='active' AND p.status IN ('joined','active') AND p.current_stage<?",
                (quest_id, order),
            )
            participants = await cursor.fetchall()
            for participant in participants:
                user_id = int(participant["user_id"])
                await connection.execute(
                    "UPDATE participant_stages SET status='skipped', completed_at=? "
                    "WHERE quest_id=? AND user_id=? AND stage_order<? AND status='open'",
                    (now, quest_id, user_id, order),
                )
                cursor = await connection.execute(
                    "INSERT OR IGNORE INTO participant_stages(quest_id,user_id,stage_id,stage_order,status,started_at) "
                    "VALUES(?,?,?,?, 'open', ?)",
                    (quest_id, user_id, stage["id"], order, now),
                )
                if cursor.rowcount == 1:
                    await connection.execute(
                        "UPDATE quest_participants SET status='active', current_stage=? WHERE quest_id=? AND user_id=?",
                        (order, quest_id, user_id),
                    )
                    activated.append(user_id)
            await connection.commit()
        return activated

    async def user_has_live_quest(self, user_id: int) -> bool:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT 1 FROM quest_participants p JOIN quests q ON q.id=p.quest_id "
                "WHERE p.user_id=? AND p.status IN ('joined','active') AND q.status IN ('scheduled','active') LIMIT 1",
                (user_id,),
            )
            return await cursor.fetchone() is not None

    async def open_stages_for_user(self, user_id: int) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT q.id AS quest_id,q.title,s.stage_order,s.question FROM quest_participants p "
                "JOIN quests q ON q.id=p.quest_id JOIN participant_stages ps ON ps.quest_id=p.quest_id AND ps.user_id=p.user_id "
                "JOIN stages s ON s.id=ps.stage_id WHERE p.user_id=? AND p.status='active' "
                "AND q.status='active' AND ps.status='open' AND s.stage_order=p.current_stage ORDER BY q.id",
                (user_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def current_open_stage(self, quest_id: int, user_id: int) -> dict[str, Any] | None:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT s.*, ps.started_at, ps.status AS session_status, p.status AS participant_status, "
                "q.status AS quest_status, q.title AS quest_title, q.progression, q.visibility "
                "FROM quest_participants p JOIN quests q ON q.id=p.quest_id "
                "JOIN participant_stages ps ON ps.quest_id=p.quest_id AND ps.user_id=p.user_id "
                "JOIN stages s ON s.id=ps.stage_id "
                "WHERE p.quest_id=? AND p.user_id=? AND s.stage_order=p.current_stage",
                (quest_id, user_id),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def submit_answer(self, quest_id: int, user_id: int, answer: str, now: str) -> dict[str, Any]:
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "SELECT q.status AS quest_status,q.progression,q.title,q.start_at,q.duration_seconds,p.status AS participant_status,"
                "p.current_stage,s.id AS stage_id,s.stage_order,s.answer_mode,s.correct_answer,s.max_attempts,s.time_limit_seconds,"
                "ps.status AS session_status,ps.started_at "
                "FROM quests q JOIN quest_participants p ON p.quest_id=q.id "
                "LEFT JOIN stages s ON s.quest_id=q.id AND s.stage_order=p.current_stage "
                "LEFT JOIN participant_stages ps ON ps.quest_id=p.quest_id AND ps.user_id=p.user_id AND ps.stage_id=s.id "
                "WHERE q.id=? AND p.user_id=?",
                (quest_id, user_id),
            )
            row = await cursor.fetchone()
            if not row:
                await connection.rollback()
                return {"code": "not_joined"}
            item = dict(row)
            if item["participant_status"] == "blocked":
                await connection.rollback()
                return {"code": "blocked"}
            if item["participant_status"] not in {"active", "joined"} or item["quest_status"] != "active":
                await connection.rollback()
                return {"code": "closed"}
            if not item["stage_id"] or item["session_status"] != "open":
                await connection.rollback()
                return {"code": "no_open_stage"}
            now_dt = datetime.fromisoformat(now)
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)
            start_dt = datetime.fromisoformat(item["start_at"])
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            duration_seconds = int(item["duration_seconds"] or 0)
            if duration_seconds and start_dt + timedelta(seconds=duration_seconds) <= now_dt:
                await connection.rollback()
                return {"code": "overall_timeout"}
            stage_limit = int(item["time_limit_seconds"] or 0)
            started_dt = datetime.fromisoformat(item["started_at"])
            if started_dt.tzinfo is None:
                started_dt = started_dt.replace(tzinfo=timezone.utc)
            if stage_limit and started_dt + timedelta(seconds=stage_limit) <= now_dt:
                await connection.execute(
                    "UPDATE participant_stages SET status='timeout',completed_at=? WHERE quest_id=? AND user_id=? AND stage_id=? AND status='open'",
                    (now, quest_id, user_id, item["stage_id"]),
                )
                await connection.execute(
                    "UPDATE quest_participants SET status='failed',completed_at=? WHERE quest_id=? AND user_id=? AND status='active'",
                    (now, quest_id, user_id),
                )
                await connection.commit()
                return {"code": "timeout"}
            attempts_cursor = await connection.execute(
                "SELECT COUNT(*) AS count FROM answers WHERE quest_id=? AND stage_id=? AND user_id=?",
                (quest_id, item["stage_id"], user_id),
            )
            attempts = int((await attempts_cursor.fetchone())["count"])
            max_attempts = int(item["max_attempts"])
            if attempts >= max_attempts:
                await connection.rollback()
                return {"code": "attempt_limit"}
            if item["answer_mode"] == "manual":
                cursor = await connection.execute(
                    "INSERT INTO answers(quest_id,stage_id,user_id,answer_text,verdict,created_at) VALUES(?,?,?,?, 'pending', ?)",
                    (quest_id, item["stage_id"], user_id, answer, now),
                )
                answer_id = int(cursor.lastrowid)
                await connection.execute(
                    "UPDATE participant_stages SET status='pending_review' WHERE quest_id=? AND user_id=? AND stage_id=?",
                    (quest_id, user_id, item["stage_id"]),
                )
                await connection.commit()
                return {"code": "pending", "answer_id": answer_id, "quest_title": item["title"]}

            correct = normalize_exact_answer(answer) == normalize_exact_answer(item["correct_answer"] or "")
            verdict = "correct" if correct else "wrong"
            await connection.execute(
                "INSERT INTO answers(quest_id,stage_id,user_id,answer_text,verdict,created_at) VALUES(?,?,?,?,?,?)",
                (quest_id, item["stage_id"], user_id, answer, verdict, now),
            )
            used = attempts + 1
            if correct:
                await connection.execute(
                    "UPDATE participant_stages SET status='correct', completed_at=? WHERE quest_id=? AND user_id=? AND stage_id=?",
                    (now, quest_id, user_id, item["stage_id"]),
                )
                cursor = await connection.execute(
                    "SELECT COUNT(*) AS count FROM stages WHERE quest_id=?", (quest_id,)
                )
                stage_count = int((await cursor.fetchone())["count"])
                is_final = int(item["stage_order"]) >= stage_count
                if is_final:
                    await connection.execute(
                        "UPDATE quest_participants SET status='completed', completed_at=? WHERE quest_id=? AND user_id=?",
                        (now, quest_id, user_id),
                    )
                await connection.commit()
                return {
                    "code": "correct",
                    "final": is_final,
                    "next_stage_order": int(item["stage_order"]) + 1 if not is_final and item["progression"] == "immediate" else None,
                    "quest_title": item["title"],
                }

            remaining = max_attempts - used
            exhausted = remaining <= 0
            if exhausted:
                await connection.execute(
                    "UPDATE participant_stages SET status='wrong', completed_at=? WHERE quest_id=? AND user_id=? AND stage_id=?",
                    (now, quest_id, user_id, item["stage_id"]),
                )
                await connection.execute(
                    "UPDATE quest_participants SET status='failed', completed_at=? WHERE quest_id=? AND user_id=?",
                    (now, quest_id, user_id),
                )
            await connection.commit()
            return {"code": "wrong", "remaining": max(0, remaining), "exhausted": exhausted}

    async def pending_answers(self, quest_id: int) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT a.id AS answer_id,a.answer_text,a.created_at,a.user_id,s.stage_order,u.full_name,u.username "
                "FROM answers a JOIN stages s ON s.id=a.stage_id JOIN users u ON u.telegram_id=a.user_id "
                "WHERE a.quest_id=? AND a.verdict='pending' ORDER BY a.created_at",
                (quest_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def get_pending_answer(self, answer_id: int) -> dict[str, Any] | None:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT a.id AS answer_id,a.quest_id,a.user_id,a.answer_text,a.created_at,s.stage_order,"
                "q.title AS quest_title,u.full_name,u.username FROM answers a "
                "JOIN stages s ON s.id=a.stage_id JOIN quests q ON q.id=a.quest_id "
                "JOIN users u ON u.telegram_id=a.user_id WHERE a.id=? AND a.verdict='pending'",
                (answer_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def review_answer(self, answer_id: int, reviewer_id: int, accepted: bool, now: str) -> dict[str, Any]:
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "SELECT a.*,s.stage_order,s.max_attempts,q.progression,q.title,p.status AS participant_status,"
                "p.current_stage AS participant_current_stage "
                "FROM answers a JOIN stages s ON s.id=a.stage_id JOIN quests q ON q.id=a.quest_id "
                "JOIN quest_participants p ON p.quest_id=a.quest_id AND p.user_id=a.user_id "
                "WHERE a.id=? AND a.verdict='pending'",
                (answer_id,),
            )
            row = await cursor.fetchone()
            if not row:
                await connection.rollback()
                return {"code": "not_pending"}
            item = dict(row)
            obsolete = int(item["participant_current_stage"]) > int(item["stage_order"])
            verdict = "correct" if accepted else "wrong"
            await connection.execute(
                "UPDATE answers SET verdict=?,reviewed_by=?,reviewed_at=? WHERE id=?",
                (verdict, reviewer_id, now, answer_id),
            )
            if accepted:
                await connection.execute(
                    "UPDATE participant_stages SET status='correct',completed_at=? "
                    "WHERE quest_id=? AND user_id=? AND stage_id=? AND status='pending_review'",
                    (now, item["quest_id"], item["user_id"], item["stage_id"]),
                )
                stage_cursor = await connection.execute(
                    "SELECT COUNT(*) AS count FROM stages WHERE quest_id=?", (item["quest_id"],)
                )
                is_final = int(item["stage_order"]) >= int((await stage_cursor.fetchone())["count"])
                if is_final:
                    await connection.execute(
                        "UPDATE quest_participants SET status='completed',completed_at=? "
                        "WHERE quest_id=? AND user_id=? AND status!='blocked'",
                        (now, item["quest_id"], item["user_id"]),
                    )
                await connection.commit()
                return {
                    "code": "reviewed",
                    "accepted": True,
                    "user_id": int(item["user_id"]),
                    "quest_id": int(item["quest_id"]),
                    "quest_title": item["title"],
                    "final": is_final,
                    "next_stage_order": int(item["stage_order"]) + 1 if not is_final and not obsolete and item["progression"] == "immediate" else None,
                    "participant_status": item["participant_status"],
                    "obsolete": obsolete,
                }

            attempt_cursor = await connection.execute(
                "SELECT COUNT(*) AS count FROM answers WHERE quest_id=? AND stage_id=? AND user_id=?",
                (item["quest_id"], item["stage_id"], item["user_id"]),
            )
            used = int((await attempt_cursor.fetchone())["count"])
            exhausted = used >= int(item["max_attempts"])
            if obsolete:
                await connection.execute(
                    "UPDATE participant_stages SET status='wrong',completed_at=? "
                    "WHERE quest_id=? AND user_id=? AND stage_id=? AND status='pending_review'",
                    (now, item["quest_id"], item["user_id"], item["stage_id"]),
                )
                exhausted = False
            elif exhausted:
                await connection.execute(
                    "UPDATE participant_stages SET status='wrong',completed_at=? "
                    "WHERE quest_id=? AND user_id=? AND stage_id=? AND status='pending_review'",
                    (now, item["quest_id"], item["user_id"], item["stage_id"]),
                )
                await connection.execute(
                    "UPDATE quest_participants SET status='failed',completed_at=? "
                    "WHERE quest_id=? AND user_id=? AND status!='blocked'",
                    (now, item["quest_id"], item["user_id"]),
                )
            else:
                await connection.execute(
                    "UPDATE participant_stages SET status='open' "
                    "WHERE quest_id=? AND user_id=? AND stage_id=? AND status='pending_review'",
                    (item["quest_id"], item["user_id"], item["stage_id"]),
                )
            await connection.commit()
            return {
                "code": "reviewed",
                "accepted": False,
                "user_id": int(item["user_id"]),
                "quest_id": int(item["quest_id"]),
                "quest_title": item["title"],
                "final": False,
                "exhausted": exhausted,
                "obsolete": obsolete,
                "remaining": max(0, int(item["max_attempts"]) - used),
                "participant_status": item["participant_status"],
            }

    async def timed_out_sessions(self, now: str) -> list[dict[str, Any]]:
        now_dt = datetime.fromisoformat(now)
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT ps.quest_id,ps.user_id,ps.stage_id,ps.stage_order,ps.started_at,s.time_limit_seconds,"
                "q.title,u.language FROM participant_stages ps JOIN stages s ON s.id=ps.stage_id "
                "JOIN quests q ON q.id=ps.quest_id JOIN users u ON u.telegram_id=ps.user_id "
                "JOIN quest_participants p ON p.quest_id=ps.quest_id AND p.user_id=ps.user_id "
                "WHERE ps.status='open' AND p.status='active' AND s.time_limit_seconds>0 AND q.status='active'"
            )
            rows = [dict(row) for row in await cursor.fetchall()]
        return [
            row for row in rows
            if datetime.fromisoformat(row["started_at"]) + timedelta(seconds=int(row["time_limit_seconds"])) <= now_dt
        ]

    async def expire_stage(self, quest_id: int, user_id: int, stage_id: int, now: str) -> bool:
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "UPDATE participant_stages SET status='timeout',completed_at=? "
                "WHERE quest_id=? AND user_id=? AND stage_id=? AND status='open'",
                (now, quest_id, user_id, stage_id),
            )
            if cursor.rowcount:
                await connection.execute(
                    "UPDATE quest_participants SET status='failed',completed_at=? "
                    "WHERE quest_id=? AND user_id=? AND status='active'",
                    (now, quest_id, user_id),
                )
            await connection.commit()
            return cursor.rowcount == 1

    async def list_participants(self, quest_id: int) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT p.*,u.username,u.full_name FROM quest_participants p "
                "JOIN users u ON u.telegram_id=p.user_id WHERE p.quest_id=? "
                "ORDER BY CASE p.status WHEN 'active' THEN 0 WHEN 'joined' THEN 1 ELSE 2 END,p.joined_at",
                (quest_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def set_participant_block(self, quest_id: int, user_id: int, reason: str | None, blocked: bool) -> bool:
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "SELECT status,previous_status FROM quest_participants WHERE quest_id=? AND user_id=?",
                (quest_id, user_id),
            )
            participant = await cursor.fetchone()
            if not participant:
                await connection.rollback()
                return False
            if blocked:
                if participant["status"] == "blocked":
                    await connection.rollback()
                    return False
                await connection.execute(
                    "UPDATE quest_participants SET status='blocked',ban_reason=?,previous_status=? WHERE quest_id=? AND user_id=?",
                    (reason, participant["status"], quest_id, user_id),
                )
            else:
                if participant["status"] != "blocked":
                    await connection.rollback()
                    return False
                restore = participant["previous_status"] or "active"
                await connection.execute(
                    "UPDATE quest_participants SET status=?,ban_reason=NULL,previous_status=NULL WHERE quest_id=? AND user_id=?",
                    (restore, quest_id, user_id),
                )
            await connection.commit()
            return True

    async def leaderboard(self, quest_id: int) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT p.user_id,p.status,p.joined_at,p.completed_at,u.full_name,u.username,"
                "COUNT(DISTINCT CASE WHEN ps.status='correct' THEN ps.stage_id END) AS solved "
                "FROM quest_participants p JOIN users u ON u.telegram_id=p.user_id "
                "LEFT JOIN participant_stages ps ON ps.quest_id=p.quest_id AND ps.user_id=p.user_id "
                "WHERE p.quest_id=? GROUP BY p.quest_id,p.user_id "
                "ORDER BY solved DESC,CASE WHEN p.completed_at IS NULL THEN 1 ELSE 0 END,p.completed_at,p.joined_at",
                (quest_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def all_participant_ids(self, quest_id: int, statuses: tuple[str, ...] = ("joined", "active")) -> list[int]:
        placeholders = ",".join("?" for _ in statuses)
        async with self._connection() as connection:
            cursor = await connection.execute(
                f"SELECT user_id FROM quest_participants WHERE quest_id=? AND status IN ({placeholders})",
                (quest_id, *statuses),
            )
            return [int(row["user_id"]) for row in await cursor.fetchall()]

    async def maybe_complete_quest(self, quest_id: int) -> bool:
        now = utc_now()
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "SELECT COUNT(*) AS total,SUM(CASE WHEN status IN ('joined','active') THEN 1 ELSE 0 END) AS remaining "
                "FROM quest_participants WHERE quest_id=?",
                (quest_id,),
            )
            result = await cursor.fetchone()
            if int(result["total"] or 0) == 0 or int(result["remaining"] or 0) > 0:
                await connection.rollback()
                return False
            cursor = await connection.execute(
                "UPDATE quests SET status='completed',completed_at=?,updated_at=? WHERE id=? AND status='active'",
                (now, now, quest_id),
            )
            if cursor.rowcount == 1:
                await connection.execute(
                    "INSERT INTO audit_logs(actor_id,action,entity_type,entity_id,details,created_at) "
                    "VALUES(NULL,'quest.auto_completed','quest',?,'{}',?)",
                    (str(quest_id), now),
                )
            await connection.commit()
            return cursor.rowcount == 1

    async def settings_get(self, key: str, default: str) -> str:
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = await cursor.fetchone()
            return str(row["value"]) if row else default

    async def settings_set(self, key: str, value: str) -> None:
        async with self._connection() as connection:
            await connection.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (key, value, utc_now()),
            )
            await connection.commit()

    async def managed_chats(self) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT * FROM managed_chats ORDER BY title COLLATE NOCASE")
            return [dict(row) for row in await cursor.fetchall()]

    async def get_managed_chat(self, chat_id: int) -> dict[str, Any] | None:
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT * FROM managed_chats WHERE chat_id=?", (chat_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def register_chat(self, chat_id: int, title: str, chat_type: str, actor_id: int) -> None:
        async with self._connection() as connection:
            await connection.execute(
                "INSERT INTO managed_chats(chat_id,title,chat_type,cleanup_enabled,added_by,created_at) VALUES(?,?,?,0,?,?) "
                "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,chat_type=excluded.chat_type",
                (chat_id, title[:200], chat_type, actor_id, utc_now()),
            )
            await connection.commit()

    async def toggle_chat_cleanup(self, chat_id: int) -> bool | None:
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute("SELECT cleanup_enabled FROM managed_chats WHERE chat_id=?", (chat_id,))
            row = await cursor.fetchone()
            if not row:
                await connection.rollback()
                return None
            new_value = 0 if row["cleanup_enabled"] else 1
            await connection.execute("UPDATE managed_chats SET cleanup_enabled=? WHERE chat_id=?", (new_value, chat_id))
            await connection.commit()
            return bool(new_value)

    async def chat_whitelist(self, chat_id: int) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM chat_whitelist WHERE chat_id=? ORDER BY user_id", (chat_id,)
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def add_chat_whitelist(self, chat_id: int, user_id: int, actor_id: int) -> bool:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "INSERT OR IGNORE INTO chat_whitelist(chat_id,user_id,added_by,created_at) VALUES(?,?,?,?)",
                (chat_id, user_id, actor_id, utc_now()),
            )
            await connection.commit()
            return cursor.rowcount == 1

    async def remove_chat_whitelist(self, chat_id: int, user_id: int) -> bool:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM chat_whitelist WHERE chat_id=? AND user_id=?", (chat_id, user_id)
            )
            await connection.commit()
            return cursor.rowcount == 1

    async def track_chat_member(self, chat_id: int, user_id: int, full_name: str, username: str | None, is_member: bool) -> None:
        now = utc_now()
        async with self._connection() as connection:
            await connection.execute(
                "INSERT INTO users(telegram_id,username,full_name,created_at,last_seen_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username,full_name=excluded.full_name,last_seen_at=excluded.last_seen_at",
                (user_id, username, full_name[:160], now, now),
            )
            await connection.execute(
                "INSERT INTO chat_members(chat_id,user_id,is_member,joined_at,last_seen_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(chat_id,user_id) DO UPDATE SET is_member=excluded.is_member,last_seen_at=excluded.last_seen_at",
                (chat_id, user_id, int(is_member), now, now),
            )
            await connection.commit()

    async def members_to_remove(self, chat_id: int) -> list[int]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT m.user_id FROM chat_members m LEFT JOIN chat_whitelist w "
                "ON w.chat_id=m.chat_id AND w.user_id=m.user_id "
                "WHERE m.chat_id=? AND m.is_member=1 AND w.user_id IS NULL ORDER BY m.user_id",
                (chat_id,),
            )
            return [int(row["user_id"]) for row in await cursor.fetchall()]

    async def get_or_create_chat_invite(
        self, quest_id: int, user_id: int, chat_id: int, invite_link: str | None = None
    ) -> str | None:
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "SELECT invite_link FROM chat_invites WHERE quest_id=? AND user_id=?", (quest_id, user_id)
            )
            row = await cursor.fetchone()
            if row:
                await connection.rollback()
                return str(row["invite_link"])
            if invite_link is None:
                await connection.rollback()
                return None
            await connection.execute(
                "INSERT OR IGNORE INTO chat_invites(quest_id,user_id,chat_id,invite_link,created_at) VALUES(?,?,?,?,?)",
                (quest_id, user_id, chat_id, invite_link, utc_now()),
            )
            cursor = await connection.execute(
                "SELECT invite_link FROM chat_invites WHERE quest_id=? AND user_id=?", (quest_id, user_id)
            )
            row = await cursor.fetchone()
            await connection.commit()
            return str(row["invite_link"]) if row else None

    async def mark_chat_invite_used(self, chat_id: int, invite_link: str) -> None:
        async with self._connection() as connection:
            await connection.execute(
                "UPDATE chat_invites SET used_at=? WHERE chat_id=? AND invite_link=? AND used_at IS NULL",
                (utc_now(), chat_id, invite_link),
            )
            await connection.commit()

    async def create_ticket(
        self, user_id: int, quest_id: int | None, target_admin_id: int | None, message: str
    ) -> int:
        now = utc_now()
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "INSERT INTO support_tickets(user_id,quest_id,target_admin_id,status,created_at,updated_at) "
                "VALUES(?,?,?,'open',?,?)",
                (user_id, quest_id, target_admin_id, now, now),
            )
            ticket_id = int(cursor.lastrowid)
            await connection.execute(
                "INSERT INTO support_messages(ticket_id,sender_id,message,created_at) VALUES(?,?,?,?)",
                (ticket_id, user_id, message, now),
            )
            await connection.commit()
            return ticket_id

    async def add_ticket_message(self, ticket_id: int, sender_id: int, message: str) -> bool:
        now = utc_now()
        async with self._connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute("SELECT status FROM support_tickets WHERE id=?", (ticket_id,))
            ticket = await cursor.fetchone()
            if not ticket or ticket["status"] != "open":
                await connection.rollback()
                return False
            await connection.execute(
                "INSERT INTO support_messages(ticket_id,sender_id,message,created_at) VALUES(?,?,?,?)",
                (ticket_id, sender_id, message, now),
            )
            await connection.execute("UPDATE support_tickets SET updated_at=? WHERE id=?", (now, ticket_id))
            await connection.commit()
            return True

    async def get_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT t.*,u.language,u.username,u.full_name FROM support_tickets t "
                "JOIN users u ON u.telegram_id=t.user_id WHERE t.id=?",
                (ticket_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def ticket_messages(self, ticket_id: int) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM support_messages WHERE ticket_id=? ORDER BY id", (ticket_id,)
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def user_tickets(self, user_id: int) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM support_tickets WHERE user_id=? ORDER BY updated_at DESC LIMIT 20", (user_id,)
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def support_admin_recipients(self, ticket_id: int) -> list[int]:
        ticket = await self.get_ticket(ticket_id)
        if not ticket:
            return []
        async with self._connection() as connection:
            if ticket["target_admin_id"]:
                cursor = await connection.execute(
                    "SELECT telegram_id FROM admins WHERE telegram_id=? UNION SELECT telegram_id FROM admins WHERE role='superadmin'",
                    (ticket["target_admin_id"],),
                )
            else:
                cursor = await connection.execute("SELECT telegram_id FROM admins WHERE role='superadmin'")
            return [int(row["telegram_id"]) for row in await cursor.fetchall()]

    async def support_quests_for_user(self, user_id: int) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT q.id,q.title,q.owner_id FROM quests q JOIN quest_participants p ON p.quest_id=q.id "
                "WHERE p.user_id=? ORDER BY p.joined_at DESC LIMIT 30",
                (user_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def statistics(self) -> dict[str, int]:
        queries = {
            "users": "SELECT COUNT(*) AS n FROM users",
            "admins": "SELECT COUNT(*) AS n FROM admins",
            "quests": "SELECT COUNT(*) AS n FROM quests",
            "active": "SELECT COUNT(*) AS n FROM quests WHERE status='active'",
            "completed": "SELECT COUNT(*) AS n FROM quests WHERE status='completed'",
            "participants": "SELECT COUNT(*) AS n FROM quest_participants",
        }
        result: dict[str, int] = {}
        async with self._connection() as connection:
            for key, query in queries.items():
                cursor = await connection.execute(query)
                row = await cursor.fetchone()
                result[key] = int(row["n"])
        return result

    async def latest_logs(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def log_action(
        self, actor_id: int | None, action: str, entity_type: str, entity_id: str | int | None, details: dict[str, Any] | None = None
    ) -> None:
        async with self._connection() as connection:
            await connection.execute(
                "INSERT INTO audit_logs(actor_id,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
                (actor_id, action, entity_type, str(entity_id) if entity_id is not None else None, json.dumps(details or {}, ensure_ascii=False), utc_now()),
            )
            await connection.commit()

    async def export_zip(self) -> bytes:
        """Export operational tables as CSV while omitting access tokens and invite URLs."""
        tables = {
            "users": "SELECT telegram_id,username,full_name,language,is_banned,ban_reason,created_at,last_seen_at FROM users",
            "admins": "SELECT * FROM admins",
            "quests": "SELECT id,owner_id,title,description,visibility,status,progression,start_at,duration_seconds,chat_id,created_at,updated_at,completed_at FROM quests",
            "stages": "SELECT * FROM stages",
            "participants": "SELECT * FROM quest_participants",
            "participant_stages": "SELECT * FROM participant_stages",
            "answers": "SELECT * FROM answers",
            "support_tickets": "SELECT * FROM support_tickets",
            "support_messages": "SELECT * FROM support_messages",
            "audit_logs": "SELECT * FROM audit_logs",
            "managed_chats": "SELECT * FROM managed_chats",
            "chat_members": "SELECT * FROM chat_members",
            "chat_whitelist": "SELECT * FROM chat_whitelist",
            "chat_invite_issuance": "SELECT quest_id,user_id,chat_id,created_at,used_at FROM chat_invites",
            "announced_stages": "SELECT * FROM announced_stages",
            "settings": "SELECT * FROM settings",
        }
        memory = io.BytesIO()
        async with self._connection() as connection:
            with zipfile.ZipFile(memory, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                for filename, query in tables.items():
                    cursor = await connection.execute(query)
                    rows = await cursor.fetchall()
                    text_buffer = io.StringIO()
                    writer = csv.writer(text_buffer)
                    if cursor.description:
                        writer.writerow([column[0] for column in cursor.description])
                    elif self.is_postgres:
                        writer.writerow(await connection.column_names(query))
                    writer.writerows([tuple(row) for row in rows])
                    archive.writestr(f"{filename}.csv", text_buffer.getvalue())
        return memory.getvalue()
