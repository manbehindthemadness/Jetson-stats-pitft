"""Application loop and command-line interface."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import random
import signal
import threading
import time

from .display import ST7789
from .inputs import InputMonitor
from .metrics import MetricCollector
from .ui import DashboardUI, mock_snapshot


LOG = logging.getLogger(__name__)


class Dashboard:
    def __init__(self, interval: float, micro_fps: float, page: int, with_inputs: bool, spi_hz: int):
        self.interval = interval
        self.micro_interval = 1.0 / micro_fps if micro_fps else 0.0
        self.page = page % len(DashboardUI.page_names)
        self.with_inputs = with_inputs
        self.spi_hz = spi_hz
        self.auto = False
        self.theme = 0
        self.auto_at = time.monotonic() + 8
        self.stop_event = threading.Event()
        self.render_event = threading.Event()
        self.lock = threading.Lock()

    def _move(self, amount: int) -> None:
        with self.lock:
            self.page = (self.page + amount) % len(DashboardUI.page_names)
            self.auto_at = time.monotonic() + 8
            LOG.info("page changed to %s", DashboardUI.page_names[self.page])
        self.render_event.set()

    def _toggle_auto(self) -> None:
        with self.lock:
            self.auto = not self.auto
            self.auto_at = time.monotonic() + 8
            LOG.info("automatic page cycling %s", "enabled" if self.auto else "disabled")
        self.render_event.set()

    def _cycle_theme(self, amount: int) -> None:
        with self.lock:
            self.theme = (self.theme + amount) % len(DashboardUI.theme_names)
            LOG.info("theme changed to %s", DashboardUI.theme_names[self.theme])
        self.render_event.set()

    def stop(self, *_args: object) -> None:
        self.stop_event.set()
        self.render_event.set()

    def run(self) -> None:
        collector = MetricCollector(interval_ms=max(250, int(self.interval * 1000)))
        inputs = None
        collector.start()
        try:
            with ST7789(speed_hz=self.spi_hz) as display:
                if self.with_inputs:
                    inputs = InputMonitor(
                        on_rotate=self._cycle_theme,
                        on_press=self._toggle_auto,
                        on_previous=lambda: self._move(-1),
                        on_next=lambda: self._move(1),
                    )
                    inputs.start()
                ui = DashboardUI()
                snapshot = collector.collect()
                with self.lock:
                    page, auto, theme = self.page, self.auto, self.theme
                ui.set_theme(theme)
                target = ui.render(snapshot, page, auto)
                display.show(target)
                displayed_page = page
                displayed_theme = theme
                displayed_auto = auto
                regions: list[tuple[int, int, int, int]] = []
                next_sample = time.monotonic() + self.interval
                next_micro = time.monotonic()
                while not self.stop_event.is_set():
                    now = time.monotonic()
                    with self.lock:
                        if self.auto and now >= self.auto_at:
                            self.page = (self.page + 1) % len(ui.page_names)
                            self.auto_at = now + 8
                        page, auto, theme = self.page, self.auto, self.theme
                    if page != displayed_page or theme != displayed_theme or auto != displayed_auto:
                        ui.set_theme(theme)
                        target = ui.render(snapshot, page, auto)
                        display.show(target)
                        displayed_page = page
                        displayed_theme = theme
                        displayed_auto = auto
                        regions.clear()
                    if now >= next_sample:
                        snapshot = collector.collect()
                        ui.set_theme(theme)
                        target = ui.render(snapshot, page, auto)
                        regions = ui.regions(page)
                        random.shuffle(regions)
                        next_sample = now + self.interval
                        if not self.micro_interval:
                            display.show(target)
                    if self.micro_interval and regions and now >= next_micro:
                        display.show_region(target, regions.pop())
                        next_micro = now + self.micro_interval
                    wake_at = min(next_sample, next_micro) if self.micro_interval and regions else next_sample
                    timeout = max(0.005, wake_at - time.monotonic())
                    self.render_event.wait(timeout)
                    self.render_event.clear()
        finally:
            if inputs:
                inputs.close()
            collector.stop()


def _page(value: str) -> int:
    lowered = value.lower()
    names = [name.lower() for name in DashboardUI.page_names]
    if lowered in names:
        return names.index(lowered)
    try:
        return int(value) % len(names)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"page must be 0-5 or one of {', '.join(names)}") from exc


def _preview(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    ui = DashboardUI()
    snapshot = mock_snapshot()
    for index, name in enumerate(ui.page_names):
        ui.render(snapshot, index).save(directory / f"{index}-{name.lower()}.png")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Fast Jetson telemetry for an ST7789 Mini PiTFT")
    parser.add_argument("--page", type=_page, default=0, help="initial page name or number")
    parser.add_argument("--interval", type=float, default=0.5, help="telemetry refresh seconds")
    parser.add_argument("--micro-fps", type=float, default=20, help="staggered widget updates/second; 0 disables")
    parser.add_argument("--spi-hz", type=int, default=4_000_000, help="SPI clock rate")
    parser.add_argument("--no-input", action="store_true", help="do not request buttons/encoder")
    parser.add_argument("--once", action="store_true", help="draw one live frame and exit")
    parser.add_argument("--preview-dir", type=Path, help="write mock page PNGs and exit")
    args = parser.parse_args()

    if args.preview_dir:
        _preview(args.preview_dir)
        return
    if args.interval < 0.1:
        parser.error("--interval must be at least 0.1 seconds")
    if args.micro_fps < 0:
        parser.error("--micro-fps cannot be negative")

    if args.once:
        collector = MetricCollector(250)
        collector.start()
        try:
            time.sleep(0.3)
            with ST7789(speed_hz=args.spi_hz) as display:
                display.show(DashboardUI().render(collector.collect(), args.page))
        finally:
            collector.stop()
        return

    dashboard = Dashboard(args.interval, args.micro_fps, args.page, not args.no_input, args.spi_hz)
    signal.signal(signal.SIGINT, dashboard.stop)
    signal.signal(signal.SIGTERM, dashboard.stop)
    dashboard.run()


if __name__ == "__main__":
    main()
