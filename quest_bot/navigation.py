"""Small breadcrumbs so every **Back** button returns to the previous screen.

Back must never skip a section: a screen remembers which list or card opened
it, and a paginated list remembers the page the user was on, so the button can
rebuild the exact callback of the previous screen. The map is keyed by
Telegram user id and lives in memory, because the navigation must survive FSM
changes (the editing flows clear the FSM state) and is only a convenience that
safely falls back to a sensible default when it is missing.
"""

from __future__ import annotations

from typing import Any

_BREADCRUMBS: dict[int, dict[str, Any]] = {}

# Breadcrumbs are only a convenience, so the oldest user is dropped once the
# map grows past this many users instead of holding memory forever.
MAX_TRACKED_USERS = 5000


def remember(user_id: int, **values: Any) -> None:
    """Store breadcrumbs for one user, keeping the values already there."""
    memory = _BREADCRUMBS.setdefault(int(user_id), {})
    memory.update(values)
    if len(_BREADCRUMBS) > MAX_TRACKED_USERS:
        _BREADCRUMBS.pop(next(iter(_BREADCRUMBS)), None)


def recall(user_id: int, key: str, default: Any = None) -> Any:
    """Read one breadcrumb, falling back to ``default`` when it is absent."""
    return _BREADCRUMBS.get(int(user_id), {}).get(key, default)


def forget(user_id: int, *keys: str) -> None:
    """Drop the given breadcrumbs, e.g. when an item they point to is gone."""
    memory = _BREADCRUMBS.get(int(user_id))
    if not memory:
        return
    for key in keys:
        memory.pop(key, None)


def clear(user_id: int) -> None:
    """Forget everything for one user, e.g. after they opened the main menu."""
    _BREADCRUMBS.pop(int(user_id), None)


DEFAULT_MANAGE_ORIGIN = "adminq:filter:all:0"


def manage_back_target(user_id: int, quest_id: int, origin: str | None = None) -> str:
    """The exact admin list the manage card was opened from."""
    if origin:
        return origin
    if int(recall(user_id, "manage_quest") or 0) == int(quest_id):
        remembered = recall(user_id, "manage_origin")
        if remembered:
            return str(remembered)
    return DEFAULT_MANAGE_ORIGIN


def reset() -> None:
    """Drop every breadcrumb; used when the process restarts or in tests."""
    _BREADCRUMBS.clear()
