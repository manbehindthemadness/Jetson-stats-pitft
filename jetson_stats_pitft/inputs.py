"""Low-overhead GPIO navigation for two buttons and a rotary encoder."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import gpiod


LOG = logging.getLogger(__name__)


LINES = {
    "encoder_a": "PAA.00",  # physical 31
    "encoder_b": "PAA.03",  # physical 37
    "encoder_sw": "PAA.01",  # physical 29
    "previous": "PAA.02",  # physical 33, rerouted PiTFT top button
    "next": "PBB.01",  # physical 16, PiTFT bottom button
}


class InputMonitor:
    _QUADRATURE = {
        0b0001: 1, 0b0111: 1, 0b1110: 1, 0b1000: 1,
        0b0010: -1, 0b1011: -1, 0b1101: -1, 0b0100: -1,
    }

    def __init__(
        self,
        on_rotate: Callable[[int], None],
        on_press: Callable[[], None],
        on_previous: Callable[[], None],
        on_next: Callable[[], None],
    ):
        self.callbacks = {
            "encoder_sw": on_press,
            "previous": on_previous,
            "next": on_next,
        }
        self.lines: dict[str, gpiod.Line] = {}
        self.on_rotate = on_rotate
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        for role, line_name in LINES.items():
            line = gpiod.find_line(line_name)
            if line is None:
                self.close()
                raise RuntimeError(f"GPIO line {line_name} ({role}) was not found")
            try:
                line.request(
                    consumer="jetson-stats-pitft",
                    type=gpiod.LINE_REQ_DIR_IN,
                    flags=gpiod.LINE_REQ_FLAG_BIAS_PULL_UP,
                )
            except OSError:
                line.request(consumer="jetson-stats-pitft", type=gpiod.LINE_REQ_DIR_IN)
            self.lines[role] = line
        self.thread = threading.Thread(target=self._run, name="pitft-input", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        previous = {name: line.get_value() for name, line in self.lines.items()}
        idle = {name: previous[name] for name in self.callbacks}
        LOG.info(
            "input idle levels: %s",
            " ".join(f"{name}={value}" for name, value in previous.items()),
        )
        encoder_state = (previous["encoder_a"] << 1) | previous["encoder_b"]
        encoder_accumulator = 0
        last_rotate = 0.0
        last_press = {name: 0.0 for name in self.callbacks}

        while not self.stop_event.wait(0.005):
            values = {name: line.get_value() for name, line in self.lines.items()}
            state = (values["encoder_a"] << 1) | values["encoder_b"]
            if state != encoder_state:
                delta = self._QUADRATURE.get((encoder_state << 2) | state, 0)
                encoder_accumulator += delta
                LOG.debug(
                    "encoder edge %d>%d delta=%+d accumulator=%+d",
                    encoder_state,
                    state,
                    delta,
                    encoder_accumulator,
                )
                encoder_state = state
                if abs(encoder_accumulator) >= 4:
                    now = time.monotonic()
                    # The compact mechanical encoder can emit more than one
                    # quadrature cycle while settling at a single detent.
                    # Coalesce that burst into one deliberate UI action.
                    if now - last_rotate >= 0.075:
                        self.on_rotate(1 if encoder_accumulator > 0 else -1)
                        last_rotate = now
                    encoder_accumulator = 0

            now = time.monotonic()
            for name, callback in self.callbacks.items():
                pressed = previous[name] == idle[name] and values[name] != idle[name]
                if pressed and now - last_press[name] > 0.15:
                    last_press[name] = now
                    LOG.info("input press: %s (%d>%d)", name, previous[name], values[name])
                    callback()
            previous = values

    def close(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
        for line in self.lines.values():
            line.release()
        self.lines.clear()
