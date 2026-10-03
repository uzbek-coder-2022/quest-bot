"""Create temporary, restorable backups of the configured database."""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


class DatabaseBackupError(RuntimeError):
    """Raised when a database backup cannot be created."""


_PG_ENV_PARAMETERS = {
    "application_name": "PGAPPNAME",
    "channel_binding": "PGCHANNELBINDING",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "gssencmode": "PGGSSENCMODE",
    "hostaddr": "PGHOSTADDR",
    "options": "PGOPTIONS",
    "service": "PGSERVICE",
    "sslcert": "PGSSLCERT",
    "sslcrl": "PGSSLCRL",
    "sslcrldir": "PGSSLCRLDIR",
    "sslkey": "PGSSLKEY",
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
}


def _postgres_connection_details(dsn: str) -> tuple[dict[str, str], tuple[str, ...] | None]:
    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    normalized = normalized.replace("postgres://", "postgresql://", 1)
    parsed = urlsplit(normalized)
    if parsed.scheme != "postgresql":
        raise DatabaseBackupError("A PostgreSQL URL is required for pg_dump.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise DatabaseBackupError("The PostgreSQL URL contains an invalid port.") from exc

    query = {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
    username = query.get("user", unquote(parsed.username or ""))
    password = query.get("password", unquote(parsed.password or ""))
    host = query.get("host", parsed.hostname or "")
    port_value = query.get("port", str(port) if port else "")
    database = query.get(
        "dbname", query.get("database", unquote(parsed.path.lstrip("/")))
    )

    inherited_variables = {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LD_LIBRARY_PATH",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TZ",
    }
    environment = {
        key: value for key, value in os.environ.items() if key in inherited_variables
    }
    environment.setdefault("PATH", "/usr/bin:/bin")
    if host:
        environment["PGHOST"] = host
    if port_value:
        environment["PGPORT"] = port_value
    if username:
        environment["PGUSER"] = username
    if database:
        environment["PGDATABASE"] = database
    environment.setdefault("PGCONNECT_TIMEOUT", "10")
    for parameter, variable in _PG_ENV_PARAMETERS.items():
        if parameter in query:
            environment[variable] = query[parameter]

    passfile_record = None
    if password:
        fields = (host or "*", port_value or "5432", database or "*", username or "*", password)
        if any("\n" in field or "\r" in field for field in fields):
            raise DatabaseBackupError("Newlines are not allowed in PostgreSQL credentials.")
        escaped = tuple(field.replace("\\", "\\\\").replace(":", "\\:") for field in fields)
        passfile_record = escaped
    return environment, passfile_record


def _write_sqlite_backup(source_path: str, target_path: str) -> None:
    if source_path == ":memory:":
        raise DatabaseBackupError("An in-memory SQLite database cannot be backed up after shutdown.")
    source = sqlite3.connect(source_path, timeout=30)
    target = sqlite3.connect(target_path, timeout=30)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


async def _write_postgres_backup(dsn: str, target_path: Path, temp_dir: Path) -> None:
    executable = shutil.which("pg_dump")
    if not executable:
        raise DatabaseBackupError(
            "pg_dump was not found. Install the PostgreSQL client package on this server."
        )
    environment, passfile_record = _postgres_connection_details(dsn)
    if passfile_record:
        passfile = temp_dir / ".pgpass"
        passfile.write_text(":".join(passfile_record) + "\n", encoding="utf-8")
        os.chmod(passfile, 0o600)
        environment["PGPASSFILE"] = str(passfile)

    process = await asyncio.create_subprocess_exec(
        executable,
        "--no-password",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        f"--file={target_path}",
        env=environment,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await process.communicate()
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()
        raise

    if process.returncode != 0:
        details = (stderr or b"").decode("utf-8", errors="replace").strip()
        safe_details = details[-2000:] if details else "no details from pg_dump"
        raise DatabaseBackupError(
            f"pg_dump exited with status {process.returncode}: {safe_details}"
        )


@asynccontextmanager
async def database_backup(dsn: str) -> AsyncIterator[Path]:
    """Yield a temporary full-database backup file and remove it on exit."""
    with tempfile.TemporaryDirectory(prefix="quest-bot-backup-") as temp_name:
        temp_dir = Path(temp_name)
        if dsn.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
            backup_path = temp_dir / "quest-bot-database.dump"
            await _write_postgres_backup(dsn, backup_path, temp_dir)
        else:
            backup_path = temp_dir / "quest-bot-database.sqlite3"
            await asyncio.to_thread(_write_sqlite_backup, dsn, str(backup_path))
        if not backup_path.is_file() or backup_path.stat().st_size == 0:
            raise DatabaseBackupError("The database backup file is missing or empty.")
        yield backup_path
