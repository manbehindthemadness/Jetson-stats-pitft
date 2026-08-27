"""Pillow-rendered 240x135 dashboard pages."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .metrics import Snapshot


W, H = 240, 135


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    header: str
    panel: str
    track: str
    divider: str
    text: str
    muted: str
    primary: str
    success: str
    accent: str
    warn: str
    danger: str


THEMES = (
    Theme("ORBIT", "#071019", "#091722", "#101e29", "#263a46", "#203441", "#e8f3f7", "#78909c", "#28d7e5", "#45e38c", "#eb5cff", "#ffc857", "#ff5964"),
    Theme("EMBER", "#160d08", "#211109", "#2a1710", "#493022", "#3b241a", "#fff2dc", "#b69a78", "#ffb000", "#ffd166", "#ff6b35", "#ffe66d", "#ff3b30"),
    Theme("MATRIX", "#020d08", "#041a10", "#092419", "#174631", "#103825", "#e5fff1", "#71a98a", "#00ff88", "#9cff57", "#00d1b2", "#e0ff4f", "#ff4d6d"),
    Theme("SYNTH", "#10071c", "#190b2b", "#21123a", "#46305e", "#38234e", "#fff0ff", "#a98ab8", "#00e5ff", "#7df9ff", "#ff4fd8", "#ffd166", "#ff477e"),
    Theme("ARCTIC", "#06101b", "#091a2a", "#10263a", "#28485f", "#1c3a50", "#edf8ff", "#809db2", "#48cae4", "#90e0ef", "#5e60ce", "#ffd166", "#ff6b6b"),
)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(FONT_DIR / name), size)


F8 = _font(8)
F9 = _font(9)
F10 = _font(10)
F11 = _font(11, True)
F13 = _font(13, True)
F18 = _font(18, True)
F24 = _font(24, True)


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def _bytes(value: float) -> str:
    for suffix in ("B/s", "K/s", "M/s", "G/s"):
        if value < 1024 or suffix == "G/s":
            return f"{value:.0f}{suffix}" if value >= 10 else f"{value:.1f}{suffix}"
        value /= 1024
    return "0B/s"


def _storage(value: float) -> str:
    for suffix in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or suffix == "TB":
            return f"{value:.1f}{suffix}"
        value /= 1024
    return "0B"


def _uptime(seconds: float) -> str:
    days, remainder = divmod(int(seconds), 86400)
    hours, minutes = divmod(remainder // 60, 60)
    return f"{days}d {hours:02}:{minutes:02}" if days else f"{hours:02}:{minutes:02}"


class DashboardUI:
    page_names = ("DECK", "CPU", "GPU", "MEMORY", "NETWORK", "SYSTEM")
    theme_names = tuple(theme.name for theme in THEMES)

    def __init__(self, theme: int = 0):
        self.theme_index = theme % len(THEMES)
        self.theme = THEMES[self.theme_index]
        self.cpu_history: deque[float] = deque([0] * 48, maxlen=48)
        self.gpu_history: deque[float] = deque([0] * 48, maxlen=48)
        self.rx_history: deque[float] = deque([0] * 48, maxlen=48)
        self.tx_history: deque[float] = deque([0] * 48, maxlen=48)

    def set_theme(self, theme: int) -> None:
        self.theme_index = theme % len(THEMES)
        self.theme = THEMES[self.theme_index]

    def render(self, snapshot: Snapshot, page: int, auto: bool = False) -> Image.Image:
        self.cpu_history.append(snapshot.cpu_percent)
        self.gpu_history.append(snapshot.gpu_percent)
        self.rx_history.append(snapshot.network_rx_bps)
        self.tx_history.append(snapshot.network_tx_bps)
        page %= len(self.page_names)
        image = Image.new("RGB", (W, H), self.theme.bg)
        draw = ImageDraw.Draw(image)
        self._header(draw, snapshot, page, auto)
        renderers = (self._deck, self._cpu, self._gpu, self._memory, self._network, self._system)
        renderers[page](draw, snapshot)
        self._footer(draw, page)
        return image

    @staticmethod
    def regions(page: int) -> list[tuple[int, int, int, int]]:
        """Semantic child regions used for staggered perceptual refresh."""
        common = [(0, 0, 120, 18), (120, 0, 240, 18), (0, 127, 240, 135)]
        page_regions = (
            [
                (0, 18, 70, 127), (70, 18, 137, 127),
                (137, 18, 240, 49), (137, 49, 240, 70),
                (137, 70, 240, 91), (137, 91, 240, 127),
            ],
            [
                (0, 18, 40, 88), (40, 18, 80, 88), (80, 18, 120, 88),
                (120, 18, 160, 88), (160, 18, 200, 88), (200, 18, 240, 88),
                (0, 88, 240, 127),
            ],
            [
                (0, 18, 82, 91), (82, 18, 240, 52), (82, 52, 240, 91),
                (0, 91, 80, 127), (80, 91, 160, 127), (160, 91, 240, 127),
            ],
            [
                (0, 18, 90, 59), (90, 18, 240, 59),
                (0, 59, 90, 90), (90, 59, 240, 90),
                (0, 90, 90, 127), (90, 90, 240, 127),
            ],
            [
                (0, 18, 120, 42), (120, 18, 240, 42),
                (0, 42, 120, 72), (120, 42, 240, 72),
                (0, 72, 80, 127), (80, 72, 160, 127), (160, 72, 240, 127),
            ],
            [
                (0, 18, 240, 53),
                (0, 53, 120, 81), (120, 53, 240, 81),
                (0, 81, 120, 105), (120, 81, 240, 105),
                (0, 105, 120, 127), (120, 105, 240, 127),
            ],
        )
        return common + page_regions[page % len(page_regions)]

    def _header(self, draw: ImageDraw.ImageDraw, s: Snapshot, page: int, auto: bool) -> None:
        draw.rectangle((0, 0, W, 18), fill=self.theme.header)
        draw.text((6, 3), self.page_names[page], font=F11, fill=self.theme.primary)
        label = f"{s.hostname[:8]}  {self.theme.name}  {'A' if auto else 'M'}"
        draw.text((234, 4), label, font=F9, fill=self.theme.success if auto else self.theme.muted, anchor="ra")

    def _footer(self, draw: ImageDraw.ImageDraw, page: int) -> None:
        for index in range(6):
            x = 105 + index * 6
            color = self.theme.primary if index == page else self.theme.track
            draw.ellipse((x, 129, x + 3, 132), fill=color)

    def _panel(self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
        draw.rounded_rectangle(box, radius=4, fill=self.theme.panel)

    def _bar(self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], value: float, color: str) -> None:
        x0, y0, x1, y1 = box
        draw.rounded_rectangle(box, radius=2, fill=self.theme.track)
        width = int((x1 - x0) * _clamp(value) / 100)
        if width:
            draw.rounded_rectangle((x0, y0, x0 + width, y1), radius=2, fill=color)

    def _spark(self, draw: ImageDraw.ImageDraw, values: deque[float], box: tuple[int, int, int, int], color: str, maximum: float = 100) -> None:
        x0, y0, x1, y1 = box
        draw.line((x0, y1, x1, y1), fill=self.theme.track)
        vals = list(values)
        if len(vals) < 2:
            return
        maximum = max(1, maximum)
        points = [
            (x0 + i * (x1 - x0) / (len(vals) - 1), y1 - _clamp(v, 0, maximum) / maximum * (y1 - y0))
            for i, v in enumerate(vals)
        ]
        draw.line(points, fill=color, width=2, joint="curve")

    def _gauge(self, draw: ImageDraw.ImageDraw, center: tuple[int, int], radius: int, value: float, color: str, label: str) -> None:
        cx, cy = center
        box = (cx - radius, cy - radius, cx + radius, cy + radius)
        draw.arc(box, 150, 390, fill=self.theme.track, width=5)
        draw.arc(box, 150, 150 + 240 * _clamp(value) / 100, fill=color, width=5)
        draw.text((cx, cy - 7), f"{value:.0f}%", font=F13, fill=self.theme.text, anchor="mm")
        draw.text((cx, cy + 8), label, font=F10, fill=self.theme.muted, anchor="mm")

    def _deck(self, draw: ImageDraw.ImageDraw, s: Snapshot) -> None:
        self._gauge(draw, (39, 66), 27, s.cpu_percent, self.theme.primary, "CPU")
        self._gauge(draw, (102, 66), 27, s.gpu_percent, self.theme.accent, "GPU")
        self._panel(draw, (137, 25, 234, 117))
        rows = (
            ("RAM", f"{s.ram_percent:.0f}%", self.theme.success),
            ("TEMP", f"{s.hottest_temp:.0f}°C", self.theme.warn),
            ("POWER", f"{s.total_power_w:.1f}W", self.theme.accent),
            ("FAN", f"{s.fan_rpm} rpm", self.theme.primary),
        )
        for i, (label, value, color) in enumerate(rows):
            y = 31 + i * 21
            draw.text((144, y), label, font=F8, fill=self.theme.muted)
            draw.text((227, y - 1), value, font=F11, fill=color, anchor="ra")
            if i < 3:
                draw.line((144, y + 16, 227, y + 16), fill=self.theme.divider)

    def _cpu(self, draw: ImageDraw.ImageDraw, s: Snapshot) -> None:
        cores = s.cpu_cores or [0.0] * 12
        for i, value in enumerate(cores[:12]):
            column, row = i % 6, i // 6
            x, y = 7 + column * 38, 27 + row * 30
            draw.text((x, y), f"{i}", font=F8, fill=self.theme.muted)
            draw.text((x + 31, y), f"{value:.0f}", font=F9, fill=self.theme.text, anchor="ra")
            self._bar(draw, (x, y + 12, x + 31, y + 17), value, self.theme.primary if row == 0 else self.theme.success)
        draw.text((7, 90), f"AVG {s.cpu_percent:.0f}%", font=F10, fill=self.theme.primary)
        draw.text((233, 90), f"LOAD {s.load[0]:.2f}", font=F10, fill=self.theme.muted, anchor="ra")
        self._spark(draw, self.cpu_history, (7, 103, 233, 123), self.theme.primary)

    def _gpu(self, draw: ImageDraw.ImageDraw, s: Snapshot) -> None:
        self._gauge(draw, (44, 65), 29, s.gpu_percent, self.theme.accent, "GR3D")
        self._panel(draw, (82, 25, 234, 84))
        draw.text((90, 31), "BOARD POWER", font=F8, fill=self.theme.muted)
        draw.text((226, 28), f"{s.total_power_w:.2f} W", font=F18, fill=self.theme.warn, anchor="ra")
        draw.text((90, 58), "HOTTEST", font=F8, fill=self.theme.muted)
        draw.text((226, 55), f"{s.hottest_temp:.1f}°C", font=F13, fill=self.theme.danger if s.hottest_temp > 80 else self.theme.success, anchor="ra")
        draw.text((7, 94), "GPU HISTORY", font=F8, fill=self.theme.muted)
        self._spark(draw, self.gpu_history, (7, 103, 233, 123), self.theme.accent)

    def _memory(self, draw: ImageDraw.ImageDraw, s: Snapshot) -> None:
        rows = (
            ("RAM", s.ram_percent, f"{s.ram_used_mb / 1024:.1f} / {s.ram_total_mb / 1024:.1f} GB", self.theme.success),
            ("SWAP", s.swap_percent, f"{s.swap_used_mb / 1024:.1f} / {s.swap_total_mb / 1024:.1f} GB", self.theme.accent),
            ("ROOT", s.disk_percent, f"{_storage(s.disk_used)} / {_storage(s.disk_total)}", self.theme.warn),
        )
        for index, (label, percent, value, color) in enumerate(rows):
            y = 28 + index * 31
            draw.text((8, y), label, font=F10, fill=color)
            draw.text((232, y), value, font=F9, fill=self.theme.text, anchor="ra")
            self._bar(draw, (8, y + 14, 232, y + 21), percent, color)
        draw.text((8, 120), "Unified memory shared by CPU + GPU", font=F8, fill=self.theme.muted)

    def _network(self, draw: ImageDraw.ImageDraw, s: Snapshot) -> None:
        draw.text((7, 25), s.network_interface, font=F13, fill=self.theme.primary)
        draw.text((233, 26), s.ip_address, font=F11, fill=self.theme.text, anchor="ra")
        draw.text((7, 47), "RX", font=F9, fill=self.theme.success)
        draw.text((27, 44), _bytes(s.network_rx_bps), font=F13, fill=self.theme.text)
        draw.text((128, 47), "TX", font=F9, fill=self.theme.accent)
        draw.text((150, 44), _bytes(s.network_tx_bps), font=F13, fill=self.theme.text)
        peak = max(1024, *self.rx_history, *self.tx_history)
        self._spark(draw, self.rx_history, (7, 73, 233, 121), self.theme.success, peak)
        self._spark(draw, self.tx_history, (7, 73, 233, 121), self.theme.accent, peak)

    def _system(self, draw: ImageDraw.ImageDraw, s: Snapshot) -> None:
        draw.text((8, 25), s.hostname, font=F24, fill=self.theme.primary)
        rows = (
            ("POWER MODE", s.nvpmodel),
            ("JETPACK", s.jetpack),
            ("L4T", s.l4t),
            ("KERNEL", s.kernel),
            ("UPTIME", _uptime(s.uptime_seconds)),
        )
        for index, (label, value) in enumerate(rows):
            y = 56 + index * 14
            draw.text((8, y), label, font=F8, fill=self.theme.muted)
            draw.text((232, y), value[:25], font=F9, fill=self.theme.text, anchor="ra")


def mock_snapshot() -> Snapshot:
    """A lively deterministic frame for docs and hardware orientation tests."""
    return Snapshot(
        hostname="artax", cpu_cores=[12, 38, 7, 63, 21, 44, 9, 76, 30, 18, 55, 25],
        cpu_percent=33.2, gpu_percent=61, ram_used_mb=8240, ram_total_mb=30670,
        swap_used_mb=128, swap_total_mb=2048,
        temperatures={"cpu": 47.2, "gpu": 45.8, "soc": 43.1},
        power_mw={"VIN_SYS_5V0": 8420}, load=(2.14, 1.87, 1.44),
        uptime_seconds=197340, disk_used=35_433_234_432, disk_total=57_982_058_496,
        network_interface="end0", ip_address="10.4.222.52",
        network_rx_bps=2_480_000, network_tx_bps=384_000, fan_rpm=1180,
        nvpmodel="MODE_30W", l4t="R39.2.1", jetpack="7.2.1-b49",
        kernel="6.8.12-1021-tegra",
    )
