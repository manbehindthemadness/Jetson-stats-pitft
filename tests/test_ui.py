"""Tests for dashboard-specific drawing behavior."""

import unittest

from PIL import Image, ImageDraw

from jetson_stats_pitft.ui import DashboardUI


class DailyBudgetBarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = DashboardUI()
        self.image = Image.new("RGB", (101, 8), self.ui.theme.bg)
        self.draw = ImageDraw.Draw(self.image)

    def test_allowance_fills_green(self) -> None:
        self.ui._daily_budget_bar(self.draw, (0, 0, 100, 7), 75)

        self.assertEqual(self.image.getpixel((50, 3)), self._rgb(self.ui.theme.success))
        self.assertEqual(self.image.getpixel((90, 3)), self._rgb(self.ui.theme.track))

    def test_overage_overlays_full_green_bar_in_red(self) -> None:
        self.ui._daily_budget_bar(self.draw, (0, 0, 100, 7), 125)

        self.assertEqual(self.image.getpixel((10, 3)), self._rgb(self.ui.theme.danger))
        self.assertEqual(self.image.getpixel((50, 3)), self._rgb(self.ui.theme.success))

    @staticmethod
    def _rgb(color: str) -> tuple[int, int, int]:
        return tuple(bytes.fromhex(color.removeprefix("#")))


if __name__ == "__main__":
    unittest.main()
