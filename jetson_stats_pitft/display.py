"""Minimal direct SPI driver for the 1.14-inch ST7789 Mini PiTFT."""

from __future__ import annotations

import time

import gpiod
import numpy as np
from PIL import Image
import spidev


class ST7789:
    native_width = 135
    native_height = 240
    width = 240
    height = 135
    x_offset = 53
    y_offset = 40
    guard_x = 52

    def __init__(self, bus: int = 0, device: int = 0, speed_hz: int = 4_000_000):
        self.dc = gpiod.find_line("PP.04")
        if self.dc is None:
            raise RuntimeError("GPIO line PP.04 (physical pin 22) was not found")
        self.dc.request(
            consumer="jetson-stats-pitft-dc",
            type=gpiod.LINE_REQ_DIR_OUT,
            default_vals=[1],
        )
        self.spi = spidev.SpiDev()
        try:
            self.spi.open(bus, device)
            self.spi.mode = 0
            self.spi.max_speed_hz = speed_hz
            self.spi.bits_per_word = 8
            self._initialize()
        except Exception:
            self.dc.release()
            raise

    def _write(self, payload: bytes | list[int]) -> None:
        self.spi.writebytes2(payload)

    def _command(self, opcode: int, data: bytes = b"", delay: float = 0.0) -> None:
        self.dc.set_value(0)
        self._write([opcode])
        if data:
            self.dc.set_value(1)
            self._write(data)
        if delay:
            time.sleep(delay)

    def _initialize(self) -> None:
        self._command(0x01, delay=0.150)  # SWRESET
        self._command(0x11, delay=0.500)  # SLPOUT
        self._command(0x3A, b"\x55")     # RGB565
        self._command(0x36, b"\x00")     # native portrait addressing
        self._command(0x21)               # INVON
        self._command(0x13, delay=0.010)  # NORON
        self._command(0x29, delay=0.100)  # DISPON
        self._clear_guard_line()

    def _clear_guard_line(self) -> None:
        """Clear the odd-centering column exposed at one rotated panel edge."""
        self._command(0x2A, self._u16(self.guard_x) + self._u16(self.guard_x))
        self._command(
            0x2B,
            self._u16(self.y_offset) + self._u16(self.y_offset + self.native_height - 1),
        )
        self._command(0x2C)
        self.dc.set_value(1)
        self._write(bytes(self.native_height * 2))

    @staticmethod
    def _u16(value: int) -> bytes:
        return bytes((value >> 8, value & 0xFF))

    @staticmethod
    def _rgb565(image: Image.Image) -> bytes:
        rgb = np.asarray(image, dtype=np.uint8)
        pixels = (
            ((rgb[:, :, 0].astype(np.uint16) & 0xF8) << 8)
            | ((rgb[:, :, 1].astype(np.uint16) & 0xFC) << 3)
            | (rgb[:, :, 2].astype(np.uint16) >> 3)
        )
        return pixels.astype(">u2", copy=False).tobytes()

    def show(self, image: Image.Image) -> None:
        if image.size != (self.width, self.height):
            raise ValueError(f"expected {self.width}x{self.height}, got {image.size}")
        portrait = image.convert("RGB").transpose(Image.Transpose.ROTATE_90)
        self._command(
            0x2A,
            self._u16(self.x_offset) + self._u16(self.x_offset + self.native_width - 1),
        )
        self._command(
            0x2B,
            self._u16(self.y_offset) + self._u16(self.y_offset + self.native_height - 1),
        )
        self._command(0x2C)
        self.dc.set_value(1)
        self._write(self._rgb565(portrait))

    def show_region(self, image: Image.Image, box: tuple[int, int, int, int]) -> None:
        """Update one logical landscape rectangle using an ST7789 address window."""
        if image.size != (self.width, self.height):
            raise ValueError(f"expected {self.width}x{self.height}, got {image.size}")
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= self.width and 0 <= y0 < y1 <= self.height):
            raise ValueError(f"invalid update region: {box}")

        # A 90-degree counter-clockwise rotation maps logical (x, y) to
        # native (y, width - 1 - x).
        portrait = image.crop(box).convert("RGB").transpose(Image.Transpose.ROTATE_90)
        native_x0 = self.x_offset + y0
        native_x1 = self.x_offset + y1 - 1
        native_y0 = self.y_offset + self.width - x1
        native_y1 = self.y_offset + self.width - x0 - 1
        self._command(0x2A, self._u16(native_x0) + self._u16(native_x1))
        self._command(0x2B, self._u16(native_y0) + self._u16(native_y1))
        self._command(0x2C)
        self.dc.set_value(1)
        self._write(self._rgb565(portrait))

    def close(self) -> None:
        self.dc.set_value(1)
        self.spi.close()
        self.dc.release()

    def __enter__(self) -> "ST7789":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
