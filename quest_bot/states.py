"""FSM states used by creation, support, and moderation wizards."""

from aiogram.fsm.state import State, StatesGroup


class CreateQuest(StatesGroup):
    title = State()
    description = State()
    visibility = State()
    stage_count = State()
    progression = State()
    start_at = State()
    duration = State()
    chat = State()
    question = State()
    answer_mode = State()
    correct_answer = State()
    max_attempts = State()
    stage_time = State()
    stage_start = State()


class EditStage(StatesGroup):
    question = State()
    answer = State()


class SupportFlow(StatesGroup):
    new_message = State()
    user_reply = State()
    admin_reply = State()


class AnswerFlow(StatesGroup):
    answer = State()


class ModerationFlow(StatesGroup):
    ban_reason = State()


class SuperadminFlow(StatesGroup):
    add_admin_id = State()
    add_chat_id = State()
    add_whitelist_id = State()
