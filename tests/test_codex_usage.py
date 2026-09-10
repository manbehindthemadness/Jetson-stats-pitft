"""Tests for reducing local Codex usage responses to LCD-safe data."""

import unittest

from jetson_stats_pitft.codex_usage import parse_usage


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


if __name__ == "__main__":
    unittest.main()
