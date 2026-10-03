"""Calendar-period helpers for aggregate leaderboards."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from .utils import LOCAL_TZ

LeaderboardPeriod = Literal["week", "month", "year"]


def current_period_bounds(
    period: LeaderboardPeriod,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Return current Tashkent period bounds as UTC ISO timestamps, end-exclusive."""
    if period not in {"week", "month", "year"}:
        raise ValueError("period must be 'week', 'month', or 'year'")

    current = now or datetime.now(LOCAL_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=LOCAL_TZ)
    else:
        current = current.astimezone(LOCAL_TZ)

    if period == "week":
        start_date = current.date() - timedelta(days=current.weekday())
    elif period == "month":
        start_date = date(current.year, current.month, 1)
    else:
        start_date = date(current.year, 1, 1)

    local_start = datetime.combine(start_date, time.min, tzinfo=LOCAL_TZ)
    start_utc = local_start.astimezone(timezone.utc).replace(microsecond=0)
    end_utc = current.astimezone(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)
    return start_utc.isoformat(), end_utc.isoformat()
