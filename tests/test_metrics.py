"""Tests for telemetry paths that differ across JetPack releases."""

from pathlib import Path
import tempfile
import unittest

from jetson_stats_pitft.metrics import _fan_rpm, _find_fan_rpm_path


class FanRpmTests(unittest.TestCase):
    def _hwmon(self, root: Path, number: int, name: str) -> Path:
        hwmon = root / f"hwmon{number}"
        hwmon.mkdir()
        (hwmon / "name").write_text(name, encoding="utf-8")
        return hwmon

    def test_nvidia_pwm_tach_rpm_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hwmon = self._hwmon(root, 3, "pwm_tach\n")
            (hwmon / "rpm").write_text("817\n", encoding="ascii")

            path = _find_fan_rpm_path(str(root))

            self.assertEqual(path, str(hwmon / "rpm"))
            self.assertEqual(_fan_rpm(path), 817)

    def test_standard_fan_input_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hwmon = self._hwmon(root, 2, "pwmfan\n")
            (hwmon / "fan1_input").write_text("1240\n", encoding="ascii")

            path = _find_fan_rpm_path(str(root))

            self.assertEqual(path, str(hwmon / "fan1_input"))
            self.assertEqual(_fan_rpm(path), 1240)

    def test_missing_or_invalid_tachometer_is_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hwmon = self._hwmon(root, 9, "pwm_tach\n")
            (hwmon / "rpm").write_text("not-a-number\n", encoding="ascii")

            self.assertEqual(_find_fan_rpm_path(str(root)), "")
            self.assertEqual(_fan_rpm(""), 0)


if __name__ == "__main__":
    unittest.main()
