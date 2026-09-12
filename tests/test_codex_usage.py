"""Tests for reducing local Codex usage responses to LCD-safe data."""

import unittest
from pathlib import Path
import tempfile

from jetson_stats_pitft.codex_usage import (
    CodexUsage,
    DailyBudgetTracker,
    UsageWindow,
    parse_usage,
)


class CodexUsageTests(unittest.TestCase):
    def test_parses_current_rate_limit_shape(self) -> None:
        result = {
            "ordinaryUsageAllowed": True,
            "rateLimitResetCredits": {"availableCount": 2},
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "planType": "self_serve_business_prolite",
                    "primary": {
                        "usedPercent": 42,
                        "windowDurationMins": 10080,
                        "resetsAt": 2000,
                    },
                    "secondary": None,
                    "credits": {"hasCredits": True, "unlimited": False, "balance": None},
                }
            },
        }

        usage = parse_usage(result, now=1000)

        self.assertEqual(usage.name, "CODEX")
        self.assertEqual(usage.plan, "BUSINESS PROLITE")
        self.assertEqual(len(usage.windows), 1)
        self.assertEqual(usage.windows[0].label, "WEEKLY")
        self.assertEqual(usage.windows[0].used_percent, 42)
        self.assertEqual(usage.windows[0].resets_at, 2000)
        self.assertEqual(usage.credits, "AVAILABLE")
        self.assertEqual(usage.reset_credits, 2)
        self.assertEqual(usage.updated_at, 1000)
        self.assertEqual(usage.error, "")

    def test_clamps_usage_and_surfaces_limit_state(self) -> None:
        result = {
            "ordinaryUsageAllowed": False,
            "rateLimits": {
                "primary": {"usedPercent": 120, "windowDurationMins": 300},
                "credits": {"unlimited": True},
                "rateLimitReachedType": "primary",
            },
        }

        usage = parse_usage(result, now=1000)

        self.assertEqual(usage.windows[0].label, "5 HOUR")
        self.assertEqual(usage.windows[0].used_percent, 100)
        self.assertEqual(usage.credits, "UNLIMITED")
        self.assertFalse(usage.ordinary_allowed)
        self.assertEqual(usage.limit_reached, "PRIMARY")


class DailyBudgetTests(unittest.TestCase):
    def _usage(self, used: int, weekly_reset: float) -> CodexUsage:
        return CodexUsage(
            windows=(UsageWindow("WEEKLY", used, weekly_reset),),
            error="",
        )

    def test_divides_remaining_allowance_over_remaining_days(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tracker = DailyBudgetTracker(Path(directory) / "budget.json")
            start = 1_000_000.0
            usage = tracker.apply(self._usage(20, start + 4 * 86400), now=start)

            self.assertAlmostEqual(usage.daily_allowance, 20.0)
            self.assertEqual(usage.daily_used, 0.0)
            self.assertEqual(usage.daily_percent, 0.0)
            self.assertEqual(usage.daily_resets_at, start + 86400)

            usage = tracker.apply(self._usage(25, start + 4 * 86400), now=start + 3600)
            self.assertEqual(usage.daily_used, 5.0)
            self.assertAlmostEqual(usage.daily_percent, 25.0)

    def test_persists_across_restart_and_rebalances_after_24_hours(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            start = 2_000_000.0
            weekly_reset = start + 4 * 86400
            DailyBudgetTracker(path).apply(self._usage(20, weekly_reset), now=start)

            restarted = DailyBudgetTracker(path)
            usage = restarted.apply(self._usage(30, weekly_reset), now=start + 3600)
            self.assertEqual(usage.daily_used, 10.0)
            self.assertAlmostEqual(usage.daily_percent, 50.0)

            usage = restarted.apply(self._usage(30, weekly_reset), now=start + 86401)
            self.assertEqual(usage.daily_used, 0.0)
            self.assertAlmostEqual(usage.daily_allowance, 70.0 / 3)
            self.assertEqual(usage.daily_resets_at, start + 2 * 86400)

    def test_weekly_rolloff_does_not_erase_daily_spend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            start = 2_500_000.0
            weekly_reset = start + 4 * 86400
            tracker = DailyBudgetTracker(path)
            tracker.apply(self._usage(20, weekly_reset), now=start)

            usage = tracker.apply(self._usage(25, weekly_reset), now=start + 3600)
            self.assertEqual(usage.daily_used, 5.0)

            usage = tracker.apply(self._usage(23, weekly_reset), now=start + 7200)
            self.assertEqual(usage.daily_used, 5.0)

            usage = tracker.apply(self._usage(24, weekly_reset), now=start + 10800)
            self.assertEqual(usage.daily_used, 6.0)
            self.assertAlmostEqual(usage.daily_percent, 30.0)

            restarted = DailyBudgetTracker(path)
            usage = restarted.apply(self._usage(25, weekly_reset), now=start + 14400)
            self.assertEqual(usage.daily_used, 7.0)

    def test_reset_timestamp_adjustment_does_not_restart_active_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tracker = DailyBudgetTracker(Path(directory) / "budget.json")
            start = 2_750_000.0
            usage = tracker.apply(self._usage(20, start + 4 * 86400), now=start)
            self.assertEqual(usage.daily_used, 0.0)

            usage = tracker.apply(
                self._usage(22, start + 4 * 86400 + 600),
                now=start + 3600,
            )
            self.assertEqual(usage.daily_used, 2.0)
            self.assertEqual(usage.daily_resets_at, start + 86400)

    def test_daily_boundaries_count_backward_from_weekly_rollover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tracker = DailyBudgetTracker(Path(directory) / "budget.json")
            start = 4_000_000.0
            until_reset = 6 * 86400 + 3 * 3600 + 19 * 60
            usage = tracker.apply(self._usage(5, start + until_reset), now=start)

            self.assertAlmostEqual(usage.daily_allowance, 95.0 / 7)
            self.assertEqual(usage.daily_resets_at, start + 3 * 3600 + 19 * 60)

    def test_new_week_starts_a_new_daily_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tracker = DailyBudgetTracker(Path(directory) / "budget.json")
            start = 3_000_000.0
            old_reset = start + 2 * 86400
            tracker.apply(self._usage(80, old_reset), now=start)

            new_reset = start + 8 * 86400
            usage = tracker.apply(self._usage(3, new_reset), now=start + 86400)
            self.assertEqual(usage.daily_used, 0.0)
            self.assertGreater(usage.daily_allowance, 0.0)

    def test_without_weekly_window_leaves_budget_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tracker = DailyBudgetTracker(Path(directory) / "budget.json")
            usage = CodexUsage(windows=(UsageWindow("5 HOUR", 10, 2000),), error="")

            result = tracker.apply(usage, now=1000)

            self.assertEqual(result.daily_allowance, 0.0)
            self.assertEqual(result.daily_percent, 0.0)


if __name__ == "__main__":
    unittest.main()
