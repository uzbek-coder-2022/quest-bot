from __future__ import annotations

import unittest
from datetime import datetime

from quest_bot.periods import current_period_bounds
from quest_bot.utils import LOCAL_TZ


class LeaderboardPeriodTests(unittest.TestCase):
    def test_week_starts_monday_in_tashkent_and_is_end_exclusive(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=LOCAL_TZ)
        start_at, end_at = current_period_bounds("week", now)
        self.assertEqual(start_at, "2025-12-28T19:00:00+00:00")
        self.assertEqual(end_at, "2026-01-01T07:00:01+00:00")

    def test_month_and_year_use_local_calendar_boundaries(self) -> None:
        now = datetime(2026, 10, 3, 12, 0, tzinfo=LOCAL_TZ)
        month_start, month_end = current_period_bounds("month", now)
        year_start, year_end = current_period_bounds("year", now)
        self.assertEqual(month_start, "2026-09-30T19:00:00+00:00")
        self.assertEqual(year_start, "2025-12-31T19:00:00+00:00")
        self.assertEqual(month_end, year_end)

    def test_naive_time_is_interpreted_as_tashkent_local_time(self) -> None:
        start_at, _ = current_period_bounds(
            "month", datetime.fromisoformat("2026-10-03T12:00:00")
        )
        self.assertEqual(start_at, "2026-09-30T19:00:00+00:00")

    def test_invalid_period_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            current_period_bounds("day")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
