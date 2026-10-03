"""Compatibility helpers for the PostgreSQL backend."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import asyncpg

_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)
_ID_INSERT = re.compile(r"^\s*INSERT\s+INTO\s+(quests|answers|support_tickets)\b", re.IGNORECASE)
_COMMAND_ROWCOUNT = re.compile(r"\s(\d+)$")


def translate_sql(query: str) -> str:
    """Translate the small set of SQLite syntax used by the repository."""
    ignored_conflicts = bool(_INSERT_OR_IGNORE.search(query))
    translated = _INSERT_OR_IGNORE.sub("INSERT INTO", query)
    translated = re.sub(
        r"\btitle\s+COLLATE\s+NOCASE\b", "lower(title)", translated, flags=re.IGNORECASE
    )
    translated = re.sub(r"\s+COLLATE\s+NOCASE\b", "", translated, flags=re.IGNORECASE)
    if ignored_conflicts:
        translated = translated.rstrip().removesuffix(";") + " ON CONFLICT DO NOTHING"

    output: list[str] = []
    parameter_number = 0
    quote: str | None = None
    index = 0
    while index < len(translated):
        character = translated[index]
        if quote:
            output.append(character)
            if character == quote:
                if index + 1 < len(translated) and translated[index + 1] == quote:
                    output.append(translated[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in {"'", '"'}:
            quote = character
            output.append(character)
        elif character == "?":
            parameter_number += 1
            output.append(f"${parameter_number}")
        else:
            output.append(character)
        index += 1
    return "".join(output)


def postgres_schema(sqlite_schema: str) -> str:
    """Adapt the shared schema to PostgreSQL's identity and wide integer types."""
    return sqlite_schema.replace(
        "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
    ).replace("INTEGER", "BIGINT")


def _command_rowcount(status: str) -> int:
    match = _COMMAND_ROWCOUNT.search(status)
    return int(match.group(1)) if match else 0


class AsyncpgCursor:
    """Expose the cursor methods used by the database repository."""

    def __init__(
        self,
        rows: Sequence[asyncpg.Record] = (),
        *,
        rowcount: int = -1,
        lastrowid: int | None = None,
    ) -> None:
        self._rows = list(rows)
        self._index = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.description = []
        if self._rows:
            self.description = [
                (name, None, None, None, None, None, None) for name, _ in self._rows[0].items()
            ]

    async def fetchone(self) -> asyncpg.Record | None:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    async def fetchall(self) -> list[asyncpg.Record]:
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows


class AsyncpgConnection:
    """Adapt asyncpg to the cursor/transaction interface used by Database."""

    def __init__(self, connection: asyncpg.Connection, transaction: asyncpg.Transaction) -> None:
        self._connection = connection
        self._transaction = transaction
        self._transaction_active = True

    async def execute(
        self, query: str, parameters: Sequence[Any] = ()
    ) -> AsyncpgCursor:
        if query.strip().upper() == "BEGIN IMMEDIATE":
            await self._connection.fetchval("SELECT pg_advisory_xact_lock($1, $2)", 913572, 1)
            return AsyncpgCursor(rowcount=0)

        sql = translate_sql(query)
        match = _ID_INSERT.match(sql)
        returns_id = bool(match and "RETURNING" not in sql.upper())
        if returns_id:
            sql = sql.rstrip().removesuffix(";") + " RETURNING id"

        statement_kind = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        if statement_kind in {"SELECT", "WITH"} or "RETURNING" in sql.upper():
            rows = await self._connection.fetch(sql, *parameters)
            lastrowid = int(rows[0]["id"]) if returns_id and rows else None
            return AsyncpgCursor(rows, rowcount=len(rows), lastrowid=lastrowid)

        status = await self._connection.execute(sql, *parameters)
        return AsyncpgCursor(rowcount=_command_rowcount(status))

    async def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                await self.execute(statement)

    async def column_names(self, query: str) -> list[str]:
        statement = await self._connection.prepare(translate_sql(query))
        return [attribute.name for attribute in statement.get_attributes()]

    async def commit(self) -> None:
        if self._transaction_active:
            await self._transaction.commit()
            self._transaction_active = False

    async def rollback(self) -> None:
        if self._transaction_active:
            await self._transaction.rollback()
            self._transaction_active = False
