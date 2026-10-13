"""Consistent severity labels and rich presentation for user feedback."""

from __future__ import annotations

from typing import Literal

from aiogram.types import InputRichMessage

from .localization import tr
from .rich_text import heading, paragraph, rich_message

FeedbackLevel = Literal["error", "warning", "success", "info"]

_FEEDBACK_MARKERS: dict[FeedbackLevel, str] = {
    "error": "❌",
    "warning": "⚠️",
    "success": "✅",
    "info": "ℹ️",
}

# These are short, user-facing status messages. Long-form screens and messages
# with their own presentation builders intentionally stay outside this mapping.
_FEEDBACK_LEVELS: dict[str, FeedbackLevel] = {
    "error_generic": "error",
    "question_archive_failed": "error",
    "participant_message_delivery_failed": "error",
    "database_backup_failed": "error",
    "database_backup_delivery_failed": "error",
    "invite_unavailable": "error",
    "quest_not_found": "warning",
    "join_closed": "warning",
    "private_link_invalid": "warning",
    "global_blocked": "warning",
    "blocked_notice": "warning",
    "warning_blocked": "warning",
    "stage_not_editable": "warning",
    "stage_schedule_invalid": "warning",
    "quest_start_edit_closed": "warning",
    "quest_duration_edit_closed": "warning",
    "quest_metadata_archived": "warning",
    "edit_quest_cover_missing": "warning",
    "stage_limit_reached": "warning",
    "chat_not_found": "warning",
    "chat_permission_missing": "warning",
    "participant_message_unavailable": "warning",
    "admin_only": "warning",
    "superadmin_only": "warning",
    "open_private_chat": "warning",
    "wrong_answer": "warning",
    "attempts_exhausted": "warning",
    "stage_timeout": "warning",
    "no_active_question": "info",
    "no_pending": "info",
    "quest_paused_notice": "info",
    "quest_stage_unavailable": "info",
    "stage_not_started_yet": "info",
    "support_ticket_closed": "info",
    "quest_ended": "info",
    "answer_pending": "info",
    "already_joined": "info",
    "cancelled": "info",
    "edit_cancelled": "info",
    "join_cancelled": "info",
    "join_success": "success",
    "created": "success",
    "pause_success": "success",
    "resume_success": "success",
    "quest_title_updated": "success",
    "quest_description_updated": "success",
    "quest_cover_updated": "success",
    "quest_cover_removed": "success",
    "quest_start_updated": "success",
    "quest_duration_updated": "success",
    "stage_added": "success",
    "stage_removed": "success",
    "stage_question_updated": "success",
    "stage_answer_updated": "success",
    "admin_added": "success",
    "admin_removed": "success",
    "blocked_success": "success",
    "unblocked_success": "success",
    "participant_message_sent": "success",
    "chat_added": "success",
    "whitelist_added": "success",
    "whitelist_removed": "success",
    "quest_finished": "success",
    "quest_archived": "success",
    "quest_unarchived": "success",
    "quest_deleted": "success",
    "quest_restored": "success",
    "quest_purged": "success",
    "review_approved": "success",
    "review_rejected": "info",
    "support_created": "success",
    "support_admin_reply_sent": "success",
    "correct_next": "success",
    "correct_wait": "success",
    "correct_done": "success",
}

# Remove only status/severity symbols already embedded in legacy translations.
# Other leading emoji (for example, a quest or stage icon) remain meaningful.
_LEADING_STATUS_MARKERS = (
    "⚠️",
    "⚠",
    "❌",
    "✅",
    "ℹ️",
    "ℹ",
    "🚫",
    "⛔️",
    "⛔",
    "🔒",
)


def feedback_level(key: str) -> FeedbackLevel:
    """Return the shared severity for a translated feedback key."""
    if key.startswith("invalid_"):
        return "warning"
    try:
        return _FEEDBACK_LEVELS[key]
    except KeyError as exc:
        raise KeyError(f"No feedback level is registered for {key!r}") from exc


def _feedback_body(language: str, key: str, values: dict[str, object]) -> str:
    body = tr(language, key, **values).lstrip()
    for marker in _LEADING_STATUS_MARKERS:
        if body.startswith(marker):
            return body[len(marker) :].lstrip(" \t\r\n:—")
    return body


def feedback_text(
    language: str,
    key: str,
    *,
    level: FeedbackLevel | None = None,
    **values: object,
) -> str:
    """Format compact feedback for callback toasts and alert popups."""
    severity = level or feedback_level(key)
    marker = _FEEDBACK_MARKERS[severity]
    body = _feedback_body(language, key, values)
    return f"{marker} {body}" if body else marker


def feedback_message(
    language: str,
    key: str,
    *,
    level: FeedbackLevel | None = None,
    **values: object,
) -> InputRichMessage:
    """Render feedback as a localized heading followed by a clear body."""
    severity = level or feedback_level(key)
    title = tr(language, f"feedback_{severity}_title")
    body = _feedback_body(language, key, values)
    blocks = [heading(title, size=3)]
    if body:
        blocks.append(paragraph(body))
    return rich_message(*blocks)
