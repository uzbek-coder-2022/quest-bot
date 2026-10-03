"""Configure private rotating application log files."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import load_dotenv

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
MAX_LOG_BYTES = 5_000_000
LOG_BACKUP_COUNT = 5


def sensitive_values(*extra_values: str) -> tuple[str, ...]:
    """Collect configured credentials that must not appear in logs or alerts."""
    load_dotenv()
    candidates = [
        *extra_values,
        os.getenv("BOT_TOKEN", ""),
        os.getenv("DATABASE_URL", ""),
    ]
    for candidate in tuple(candidates):
        if "://" not in candidate:
            continue
        try:
            normalized = candidate.replace("postgresql+asyncpg://", "postgresql://", 1)
            password = urlsplit(normalized).password
        except ValueError:
            password = None
        if password:
            candidates.append(password)
            decoded_password = unquote(password)
            if len(decoded_password) >= 4:
                candidates.append(decoded_password)
    return tuple(dict.fromkeys(value for value in candidates if value))


def redact_sensitive_text(text: str, values: Iterable[str] = ()) -> str:
    """Replace configured tokens and database credentials before output."""
    for secret in sorted(set(values), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return text


class RedactingFormatter(logging.Formatter):
    """Format log records while removing configured secrets, including tracebacks."""

    def __init__(self, fmt: str, secrets: Iterable[str]) -> None:
        super().__init__(fmt)
        self.secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_text(super().format(record), self.secrets)


def configure_logging(
    log_dir: str | Path | None = None,
    extra_secrets: Iterable[str] = (),
) -> tuple[Path, Path]:
    """Write all application logs to `log` and errors to `log_err`, plus stderr."""
    root_dir = Path(__file__).resolve().parent.parent
    selected_dir = Path(
        log_dir or os.getenv("QUEST_BOT_LOG_DIR", str(root_dir / "logs"))
    ).resolve()
    selected_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        selected_dir.chmod(0o700)
    except OSError:
        pass

    log_path = selected_dir / "log"
    error_path = selected_dir / "log_err"
    formatter = RedactingFormatter(LOG_FORMAT, sensitive_values(*extra_secrets))
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        RotatingFileHandler(
            log_path,
            maxBytes=MAX_LOG_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        ),
        RotatingFileHandler(
            error_path,
            maxBytes=MAX_LOG_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        ),
    ]
    handlers[0].setLevel(logging.INFO)
    handlers[1].setLevel(logging.INFO)
    handlers[2].setLevel(logging.ERROR)
    for handler in handlers:
        handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    for previous_handler in root_logger.handlers[:]:
        root_logger.removeHandler(previous_handler)
        previous_handler.close()
    root_logger.setLevel(logging.INFO)
    for handler in handlers:
        root_logger.addHandler(handler)

    for path in (log_path, error_path):
        try:
            path.chmod(0o600)
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return log_path, error_path
