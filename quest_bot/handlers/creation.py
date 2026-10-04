"""Admin-only guided quest creation flow."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..config import Settings
from ..database import Database
from ..keyboards import (
    admin_home_keyboard,
    creation_answer_mode,
    creation_attempts_mode,
    creation_chat,
    creation_cover_keyboard,
    creation_progression,
    creation_visibility,
)
from ..localization import tr
from ..services import (
    answer_deep_link,
    archive_question_message,
    delete_archived_message,
    question_media_details,
)
from ..states import CreateQuest
from ..utils import format_datetime, parse_local_datetime

router = Router(name="creation")
logger = logging.getLogger(__name__)


def _extract_question_text(message: Message) -> str | None:
    """Return a valid text/caption for a supported question message."""
    if message.text is not None:
        question = message.text.strip()
        return question if _valid_text(question, 4096) else None
    if message.photo or message.video:
        caption = (message.caption or "").strip()
        return caption if len(caption) <= 1024 else None
    return None


def _valid_text(value: str | None, limit: int = 1000) -> bool:
    return bool(value and value.strip() and len(value.strip()) <= limit)


async def _authorized(message_or_callback, db: Database) -> bool:
    user = message_or_callback.from_user
    return bool(user and await db.get_role(user.id) in {"admin", "superadmin"})


async def _ask_stage_question(message: Message, state: FSMContext, language: str) -> None:
    data = await state.get_data()
    await state.set_state(CreateQuest.question)
    await message.answer(tr(language, "ask_question", number=data["current_stage"]))


async def _ask_first_question(message: Message, state: FSMContext, language: str) -> None:
    await state.set_state(CreateQuest.question)
    await message.answer(tr(language, "ask_question", number=1))


async def _ask_attempts_or_stage_time(
    message: Message, state: FSMContext, language: str
) -> None:
    """Ask the attempt limit here, or reuse the shared value chosen up front."""
    data = await state.get_data()
    if data.get("attempts_mode") == "same":
        draft = dict(data.get("stage_draft", {}))
        draft["max_attempts"] = int(data.get("common_max_attempts") or 1)
        await state.update_data(stage_draft=draft)
        await state.set_state(CreateQuest.stage_time)
        await message.answer(tr(language, "ask_stage_time"))
        return
    await state.set_state(CreateQuest.max_attempts)
    await message.answer(tr(language, "ask_attempts"))


async def _ask_stage_start(message: Message, state: FSMContext, language: str, db: Database) -> None:
    data = await state.get_data()
    stage_number = int(data["current_stage"])
    if data["progression"] == "scheduled" and stage_number > 1:
        await state.set_state(CreateQuest.stage_start)
        await message.answer(tr(language, "ask_stage_start", number=stage_number))
        return
    await _save_stage_or_continue(message, state, data["start_at"], language, db)


async def _save_stage_or_continue(message: Message, state: FSMContext, starts_at: str, language: str, db: Database) -> None:
    data = await state.get_data()
    stage = dict(data["stage_draft"])
    stage["starts_at"] = starts_at
    if data.get("attempts_mode") == "same":
        stage["max_attempts"] = int(data.get("common_max_attempts") or 1)
    stages = list(data.get("stages", []))
    stages.append(stage)
    current = int(data["current_stage"])
    if current < int(data["stage_count"]):
        await state.update_data(stages=stages, current_stage=current + 1, stage_draft={})
        await _ask_stage_question(message, state, language)
        return

    if await db.get_role(message.from_user.id) not in {"admin", "superadmin"}:
        await state.clear()
        await message.answer(tr(language, "admin_only"))
        return

    quest_data = {
        "title": data["title"],
        "description": data["description"],
        "visibility": data["visibility"],
        "progression": data["progression"],
        "start_at": data["start_at"],
        "duration_seconds": data["duration_seconds"],
        "chat_id": data.get("chat_id"),
        "cover_chat_id": data.get("cover_chat_id"),
        "cover_message_id": data.get("cover_message_id"),
        "cover_file_id": data.get("cover_file_id"),
        "invite_token": secrets.token_urlsafe(9),
    }
    created_id = await db.create_quest(message.from_user.id, quest_data, stages)
    await state.clear()
    result = tr(
        language,
        "created",
        title=quest_data["title"],
        start=format_datetime(quest_data["start_at"], language),
    )
    if quest_data["visibility"] == "private":
        bot: Bot = message.bot
        me = await bot.get_me()
        invite_url = answer_deep_link(me.username or "", {**quest_data, "id": created_id})
        result += f"\n\n{invite_url}"
    await message.answer(result, reply_markup=admin_home_keyboard(language))


@router.callback_query(F.data == "create:start")
async def creation_start(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    language = await db.get_language(callback.from_user.id)
    if not callback.message or callback.message.chat.type != "private":
        await callback.answer(tr(language, "open_private_chat"), show_alert=True)
        return
    if not await _authorized(callback, db):
        await callback.answer(tr(language, "admin_only"), show_alert=True)
        return
    await state.clear()
    await state.set_state(CreateQuest.title)
    if callback.message:
        await callback.message.answer(tr(language, "ask_title"))
    await callback.answer()


@router.message(CreateQuest.title)
async def title_received(message: Message, state: FSMContext, db: Database) -> None:
    language = await db.get_language(message.from_user.id)
    title = message.text.strip() if message.text else ""
    if not _valid_text(title, 100):
        await message.answer(tr(language, "invalid_text"))
        return
    await state.update_data(title=title)
    await state.set_state(CreateQuest.description)
    await message.answer(tr(language, "ask_description"))


@router.message(CreateQuest.description)
async def description_received(message: Message, state: FSMContext, db: Database) -> None:
    language = await db.get_language(message.from_user.id)
    description = message.text.strip() if message.text else ""
    if not _valid_text(description, 1000):
        await message.answer(tr(language, "invalid_text"))
        return
    await state.update_data(description=description)
    await state.set_state(CreateQuest.cover_photo)
    await message.answer(tr(language, "ask_cover_photo"), reply_markup=creation_cover_keyboard(language))


@router.message(CreateQuest.cover_photo, F.photo)
async def cover_photo_received(
    message: Message, state: FSMContext, db: Database, settings: Settings
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    if not await _authorized(message, db):
        await state.clear()
        await message.answer(tr(language, "admin_only"))
        return
    try:
        cover_chat_id, cover_message_id = await archive_question_message(
            message.bot, message, settings.question_archive_channel_id
        )
    except TelegramAPIError:
        logger.exception("Could not archive a quest cover from admin %s", message.from_user.id)
        await message.answer(tr(language, "question_archive_failed"))
        return
    await state.update_data(
        cover_chat_id=cover_chat_id,
        cover_message_id=cover_message_id,
        cover_file_id=message.photo[-1].file_id,
    )
    await state.set_state(CreateQuest.visibility)
    await message.answer(tr(language, "ask_visibility"), reply_markup=creation_visibility(language))


@router.message(CreateQuest.cover_photo)
async def invalid_cover_photo(message: Message, db: Database) -> None:
    language = await db.get_language(message.from_user.id if message.from_user else 0)
    await message.answer(tr(language, "invalid_cover_photo"), reply_markup=creation_cover_keyboard(language))


@router.callback_query(CreateQuest.cover_photo, F.data == "create:cover:skip")
async def skip_cover_photo(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    language = await db.get_language(callback.from_user.id)
    await state.update_data(
        cover_chat_id=None, cover_message_id=None, cover_file_id=None
    )
    await state.set_state(CreateQuest.visibility)
    if callback.message:
        await callback.message.answer(tr(language, "ask_visibility"), reply_markup=creation_visibility(language))
    await callback.answer()


@router.callback_query(CreateQuest.visibility, F.data.startswith("create:visibility:"))
async def visibility_selected(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    visibility = callback.data.rsplit(":", 1)[1]
    if visibility not in {"public", "private"}:
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    await state.update_data(visibility=visibility)
    await state.set_state(CreateQuest.stage_count)
    if callback.message:
        await callback.message.answer(tr(language, "ask_stage_count"))
    await callback.answer()


@router.message(CreateQuest.stage_count)
async def stage_count_received(message: Message, state: FSMContext, db: Database) -> None:
    language = await db.get_language(message.from_user.id)
    try:
        count = int((message.text or "").strip())
    except ValueError:
        await message.answer(tr(language, "invalid_number"))
        return
    if not 1 <= count <= 30:
        await message.answer(tr(language, "invalid_number"))
        return
    await state.update_data(stage_count=count)
    await state.set_state(CreateQuest.progression)
    await message.answer(tr(language, "ask_progression"), reply_markup=creation_progression(language))


@router.callback_query(CreateQuest.progression, F.data.startswith("create:progression:"))
async def progression_selected(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    progression = callback.data.rsplit(":", 1)[1]
    if progression not in {"immediate", "scheduled"}:
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    await state.update_data(progression=progression)
    await state.set_state(CreateQuest.start_at)
    if callback.message:
        await callback.message.answer(tr(language, "ask_start"))
    await callback.answer()


@router.message(CreateQuest.start_at)
async def start_time_received(message: Message, state: FSMContext, db: Database) -> None:
    language = await db.get_language(message.from_user.id)
    start_at = parse_local_datetime(message.text or "")
    if not start_at:
        await message.answer(tr(language, "invalid_datetime"))
        return
    # A quest needs a short lead time so participants can see it before the
    # first stage starts.
    if datetime.fromisoformat(start_at) < datetime.now(timezone.utc) + timedelta(
        minutes=10
    ):
        await message.answer(tr(language, "invalid_quest_start_min"))
        return
    await state.update_data(start_at=start_at)
    await state.set_state(CreateQuest.duration)
    await message.answer(tr(language, "ask_duration"))


@router.message(CreateQuest.duration)
async def duration_received(message: Message, state: FSMContext, db: Database) -> None:
    language = await db.get_language(message.from_user.id)
    try:
        minutes = int((message.text or "").strip())
    except ValueError:
        await message.answer(tr(language, "invalid_number"))
        return
    if not 0 <= minutes <= 525600:
        await message.answer(tr(language, "invalid_number"))
        return
    await state.update_data(duration_seconds=minutes * 60)
    chats = await db.managed_chats()
    await state.set_state(CreateQuest.chat)
    await message.answer(tr(language, "ask_chat"), reply_markup=creation_chat(language, chats))


@router.callback_query(CreateQuest.chat, F.data.startswith("create:chat:"))
async def chat_selected(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    try:
        chat_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    if chat_id and not await db.get_managed_chat(chat_id):
        await callback.answer(tr(await db.get_language(callback.from_user.id), "chat_not_found"), show_alert=True)
        return
    language = await db.get_language(callback.from_user.id)
    await state.update_data(
        chat_id=chat_id or None,
        current_stage=1,
        stages=[],
        stage_draft={},
        attempts_mode=None,
        common_max_attempts=None,
    )
    await state.set_state(CreateQuest.attempts_mode)
    if callback.message:
        await callback.message.answer(
            tr(language, "ask_attempts_mode"),
            reply_markup=creation_attempts_mode(language),
        )
    await callback.answer()


@router.callback_query(CreateQuest.attempts_mode, F.data == "create:attempts:same")
async def attempts_mode_same(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    language = await db.get_language(callback.from_user.id)
    await state.update_data(attempts_mode="same")
    await state.set_state(CreateQuest.common_attempts)
    if callback.message:
        await callback.message.answer(tr(language, "ask_common_attempts"))
    await callback.answer()


@router.callback_query(CreateQuest.attempts_mode, F.data == "create:attempts:different")
async def attempts_mode_different(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    language = await db.get_language(callback.from_user.id)
    await state.update_data(attempts_mode="different")
    if callback.message:
        await _ask_first_question(callback.message, state, language)
    await callback.answer()


@router.message(CreateQuest.common_attempts)
async def common_attempts_received(message: Message, state: FSMContext, db: Database) -> None:
    language = await db.get_language(message.from_user.id)
    try:
        attempts = int((message.text or "").strip())
    except ValueError:
        await message.answer(tr(language, "invalid_number"))
        return
    if not 1 <= attempts <= 100:
        await message.answer(tr(language, "invalid_number"))
        return
    await state.update_data(common_max_attempts=attempts)
    await _ask_first_question(message, state, language)


@router.message(CreateQuest.question)
async def question_received(
    message: Message, state: FSMContext, db: Database, settings: Settings
) -> None:
    if not message.from_user:
        return
    language = await db.get_language(message.from_user.id)
    question = _extract_question_text(message)
    if question is None:
        await message.answer(tr(language, "invalid_question"))
        return
    question_media_type, question_file_id = question_media_details(message)
    if not await _authorized(message, db):
        await state.clear()
        await message.answer(tr(language, "admin_only"))
        return
    try:
        archive_chat_id, archive_message_id = await archive_question_message(
            message.bot, message, settings.question_archive_channel_id
        )
    except TelegramAPIError:
        logger.exception("Could not archive a quest question from admin %s", message.from_user.id)
        await message.answer(tr(language, "question_archive_failed"))
        return
    await state.update_data(
        stage_draft={
            "question": question,
            "question_media_type": question_media_type,
            "question_file_id": question_file_id,
            "source_chat_id": archive_chat_id,
            "source_message_id": archive_message_id,
        }
    )
    await state.set_state(CreateQuest.answer_mode)
    await message.answer(tr(language, "ask_answer_mode"), reply_markup=creation_answer_mode(language))


@router.callback_query(CreateQuest.answer_mode, F.data.startswith("create:answer:"))
async def answer_mode_selected(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    mode = callback.data.rsplit(":", 1)[1]
    if mode not in {"auto", "manual"}:
        await callback.answer()
        return
    language = await db.get_language(callback.from_user.id)
    data = await state.get_data()
    draft = dict(data.get("stage_draft", {}))
    draft["answer_mode"] = mode
    await state.update_data(stage_draft=draft)
    if mode == "auto":
        await state.set_state(CreateQuest.correct_answer)
        if callback.message:
            await callback.message.answer(tr(language, "ask_correct_answer"))
    else:
        draft["correct_answer"] = None
        await state.update_data(stage_draft=draft)
        if callback.message:
            await _ask_attempts_or_stage_time(callback.message, state, language)
    await callback.answer()


@router.message(CreateQuest.correct_answer)
async def correct_answer_received(message: Message, state: FSMContext, db: Database) -> None:
    language = await db.get_language(message.from_user.id)
    answer = message.text if message.text is not None else ""
    if not answer.strip() or len(answer) > 300:
        await message.answer(tr(language, "invalid_text"))
        return
    data = await state.get_data()
    draft = dict(data.get("stage_draft", {}))
    draft["correct_answer"] = answer
    await state.update_data(stage_draft=draft)
    await _ask_attempts_or_stage_time(message, state, language)


@router.message(CreateQuest.max_attempts)
async def attempts_received(message: Message, state: FSMContext, db: Database) -> None:
    language = await db.get_language(message.from_user.id)
    try:
        attempts = int((message.text or "").strip())
    except ValueError:
        await message.answer(tr(language, "invalid_number"))
        return
    if not 1 <= attempts <= 100:
        await message.answer(tr(language, "invalid_number"))
        return
    data = await state.get_data()
    draft = dict(data.get("stage_draft", {}))
    draft["max_attempts"] = attempts
    await state.update_data(stage_draft=draft)
    await state.set_state(CreateQuest.stage_time)
    await message.answer(tr(language, "ask_stage_time"))


@router.message(CreateQuest.stage_time)
async def stage_time_received(message: Message, state: FSMContext, db: Database) -> None:
    language = await db.get_language(message.from_user.id)
    try:
        minutes = int((message.text or "").strip())
    except ValueError:
        await message.answer(tr(language, "invalid_number"))
        return
    if not 0 <= minutes <= 525600:
        await message.answer(tr(language, "invalid_number"))
        return
    data = await state.get_data()
    draft = dict(data.get("stage_draft", {}))
    draft["time_limit_seconds"] = minutes * 60
    await state.update_data(stage_draft=draft)
    await _ask_stage_start(message, state, language, db)


@router.message(CreateQuest.stage_start)
async def stage_start_received(message: Message, state: FSMContext, db: Database) -> None:
    language = await db.get_language(message.from_user.id)
    starts_at = parse_local_datetime(message.text or "")
    data = await state.get_data()
    previous = data["stages"][-1]["starts_at"] if data.get("stages") else data["start_at"]
    duration_seconds = int(data.get("duration_seconds", 0))
    valid = bool(starts_at and starts_at > previous)
    if valid and duration_seconds:
        total_end = datetime.fromisoformat(data["start_at"]) + timedelta(seconds=duration_seconds)
        valid = datetime.fromisoformat(starts_at) <= total_end
    if not valid:
        await message.answer(tr(language, "invalid_datetime"))
        return
    await _save_stage_or_continue(message, state, starts_at, language, db)


@router.callback_query(F.data == "create:cancel")
async def creation_cancel(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    await state.clear()
    language = await db.get_language(callback.from_user.id)
    if callback.message:
        archive_messages = list(data.get("stages", []))
        if data.get("stage_draft"):
            archive_messages.append(data["stage_draft"])
        for stage in archive_messages:
            await delete_archived_message(
                callback.message.bot,
                stage.get("source_chat_id"),
                stage.get("source_message_id"),
            )
        await delete_archived_message(
            callback.message.bot, data.get("cover_chat_id"), data.get("cover_message_id")
        )
        await callback.message.answer(tr(language, "cancelled"), reply_markup=admin_home_keyboard(language))
    await callback.answer()
