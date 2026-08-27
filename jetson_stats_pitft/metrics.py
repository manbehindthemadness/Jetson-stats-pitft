"""Dependency-light Jetson and Linux telemetry collection."""

from __future__ import annotations

from dataclasses import dataclass, field
import glob
import os
import re
import socket
import struct
import subprocess
import threading
import time
from typing import Any


RAM_RE = re.compile(r"RAM\s+(\d+)/(\d+)MB")
SWAP_RE = re.compile(r"SWAP\s+(\d+)/(\d+)MB")
CPU_RE = re.compile(r"CPU\s+\[([^]]+)\]")
GPU_RE = re.compile(r"GR3D_FREQ\s+(\d+)%")
TEMP_RE = re.compile(r"([A-Za-z0-9_]+)@([0-9.]+)C")
POWER_RE = re.compile(r"([A-Z][A-Z0-9_]+)\s+(\d+)mW(?:/(\d+)mW)?(?:/(\d+)mW)?")


@dataclass
class Snapshot:
    timestamp: float = field(default_factory=time.time)
    hostname: str = "jetson"
    cpu_cores: list[float] = field(default_factory=list)
    cpu_freqs: list[int] = field(default_factory=list)
    cpu_percent: float = 0.0
    gpu_percent: float = 0.0
    ram_used_mb: int = 0
    ram_total_mb: int = 1
    swap_used_mb: int = 0
    swap_total_mb: int = 0
    temperatures: dict[str, float] = field(default_factory=dict)
    power_mw: dict[str, int] = field(default_factory=dict)
    load: tuple[float, float, float] = (0.0, 0.0, 0.0)
    uptime_seconds: float = 0.0
    disk_used: int = 0
    disk_total: int = 1
    network_interface: str = "—"
    ip_address: str = "—"
    network_rx_bps: float = 0.0
    network_tx_bps: float = 0.0
    fan_rpm: int = 0
    nvpmodel: str = "unknown"
    l4t: str = "unknown"
    jetpack: str = "unknown"
    kernel: str = "unknown"

    @property
    def ram_percent(self) -> float:
        return 100.0 * self.ram_used_mb / max(1, self.ram_total_mb)

    @property
    def swap_percent(self) -> float:
        return 100.0 * self.swap_used_mb / max(1, self.swap_total_mb) if self.swap_total_mb else 0.0

    @property
    def disk_percent(self) -> float:
        return 100.0 * self.disk_used / max(1, self.disk_total)

    @property
    def hottest_temp(self) -> float:
        return max(self.temperatures.values(), default=0.0)

    @property
    def total_power_w(self) -> float:
        preferred = self.power_mw.get("VIN_SYS_5V0")
        if preferred is not None:
            return preferred / 1000.0
        return sum(self.power_mw.values()) / 1000.0


def parse_tegrastats(line: str) -> dict[str, Any]:
    """Parse the stable fields used by this display from one tegrastats line."""
    result: dict[str, Any] = {}

    match = RAM_RE.search(line)
    if match:
        result["ram_used_mb"], result["ram_total_mb"] = map(int, match.groups())

    match = SWAP_RE.search(line)
    if match:
        result["swap_used_mb"], result["swap_total_mb"] = map(int, match.groups())

    match = CPU_RE.search(line)
    if match:
        cores: list[float] = []
        freqs: list[int] = []
        for field_text in match.group(1).split(","):
            field_text = field_text.strip()
            if field_text == "off":
                cores.append(0.0)
                freqs.append(0)
                continue
            cpu_match = re.match(r"(\d+)%@?(\d+)?", field_text)
            if cpu_match:
                cores.append(float(cpu_match.group(1)))
                freqs.append(int(cpu_match.group(2) or 0))
        result["cpu_cores"] = cores
        result["cpu_freqs"] = freqs
        result["cpu_percent"] = sum(cores) / len(cores) if cores else 0.0

    match = GPU_RE.search(line)
    if match:
        result["gpu_percent"] = float(match.group(1))

    result["temperatures"] = {
        name.lower(): float(value) for name, value in TEMP_RE.findall(line)
    }
    result["power_mw"] = {
        name: int(current) for name, current, _average, _peak in POWER_RE.findall(line)
    }
    return result


class TegrastatsReader:
    def __init__(self, interval_ms: int = 500):
        self.interval_ms = interval_ms
        self.latest: dict[str, Any] = {}
        self.last_line = ""
        self.error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="tegrastats", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._process = subprocess.Popen(
                ["/usr/bin/tegrastats", "--interval", str(self.interval_ms)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert self._process.stdout is not None
            for line in self._process.stdout:
                if self._stop.is_set():
                    break
                parsed = parse_tegrastats(line)
                with self._lock:
                    self.latest = parsed
                    self.last_line = line.rstrip()
        except Exception as exc:  # The UI should remain alive with Linux metrics.
            self.error = str(exc)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.latest)

    def stop(self) -> None:
        self._stop.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if self._thread:
            self._thread.join(timeout=2)


def _read_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return default


def _command_text(command: list[str], default: str = "unknown") -> str:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=3).strip()
    except (OSError, subprocess.SubprocessError):
        return default


def _default_interface() -> str:
    try:
        with open("/proc/net/route", "r", encoding="ascii") as handle:
            next(handle, None)
            for line in handle:
                fields = line.split()
                if len(fields) > 3 and fields[1] == "00000000" and int(fields[3], 16) & 2:
                    return fields[0]
    except (OSError, ValueError):
        pass
    return ""


def _ipv4_address(interface: str) -> str:
    if not interface:
        return "—"
    try:
        import fcntl

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        request = struct.pack("256s", interface.encode("ascii")[:15])
        response = fcntl.ioctl(sock.fileno(), 0x8915, request)
        return socket.inet_ntoa(response[20:24])
    except OSError:
        return "—"


def _fan_rpm() -> int:
    for path in glob.glob("/sys/class/hwmon/hwmon*/fan*_input"):
        value = _read_text(path)
        if value.isdigit():
            return int(value)
    return 0


class MetricCollector:
    def __init__(self, interval_ms: int = 500):
        self.tegrastats = TegrastatsReader(interval_ms)
        self.hostname = socket.gethostname()
        self.kernel = os.uname().release
        self.l4t = self._read_l4t()
        self.jetpack = _command_text(
            ["dpkg-query", "-W", "-f=${Version}", "nvidia-jetpack"]
        )
        nvpmodel = _command_text(["nvpmodel", "-q"], "unknown")
        self.nvpmodel = nvpmodel.replace("NV Power Mode:", "").splitlines()[0].strip()
        self._last_network: tuple[float, int, int] | None = None
        self._cached_interface = _default_interface()
        self._cached_ip = _ipv4_address(self._cached_interface)
        self._slow_poll_at = 0.0

    @staticmethod
    def _read_l4t() -> str:
        release = _read_text("/etc/nv_tegra_release", "")
        match = re.search(r"# R(\d+) \(release\), REVISION: ([0-9.]+)", release)
        return f"R{match.group(1)}.{match.group(2)}" if match else "unknown"

    def start(self) -> None:
        self.tegrastats.start()

    def stop(self) -> None:
        self.tegrastats.stop()

    def collect(self) -> Snapshot:
        data = self.tegrastats.snapshot()
        now = time.monotonic()

        try:
            load = os.getloadavg()
        except OSError:
            load = (0.0, 0.0, 0.0)

        uptime_text = _read_text("/proc/uptime", "0")
        try:
            uptime = float(uptime_text.split()[0])
        except (ValueError, IndexError):
            uptime = 0.0

        disk = os.statvfs("/")
        disk_total = disk.f_blocks * disk.f_frsize
        disk_used = disk_total - disk.f_bavail * disk.f_frsize

        # Route and address discovery use ioctls/proc parsing; refresh them only
        # occasionally while keeping counters and the display responsive.
        if now >= self._slow_poll_at:
            self._cached_interface = _default_interface()
            self._cached_ip = _ipv4_address(self._cached_interface)
            self._slow_poll_at = now + 5.0
        interface = self._cached_interface
        rx = int(_read_text(f"/sys/class/net/{interface}/statistics/rx_bytes", "0") or 0)
        tx = int(_read_text(f"/sys/class/net/{interface}/statistics/tx_bytes", "0") or 0)
        rx_bps = tx_bps = 0.0
        if self._last_network and interface:
            then, old_rx, old_tx = self._last_network
            elapsed = max(0.001, now - then)
            rx_bps = max(0.0, (rx - old_rx) / elapsed)
            tx_bps = max(0.0, (tx - old_tx) / elapsed)
        self._last_network = (now, rx, tx)

        return Snapshot(
            hostname=self.hostname,
            cpu_cores=data.get("cpu_cores", []),
            cpu_freqs=data.get("cpu_freqs", []),
            cpu_percent=data.get("cpu_percent", 0.0),
            gpu_percent=data.get("gpu_percent", 0.0),
            ram_used_mb=data.get("ram_used_mb", 0),
            ram_total_mb=data.get("ram_total_mb", 1),
            swap_used_mb=data.get("swap_used_mb", 0),
            swap_total_mb=data.get("swap_total_mb", 0),
            temperatures=data.get("temperatures", {}),
            power_mw=data.get("power_mw", {}),
            load=load,
            uptime_seconds=uptime,
            disk_used=disk_used,
            disk_total=disk_total,
            network_interface=interface or "—",
            ip_address=self._cached_ip,
            network_rx_bps=rx_bps,
            network_tx_bps=tx_bps,
            fan_rpm=_fan_rpm(),
            nvpmodel=self.nvpmodel,
            l4t=self.l4t,
            jetpack=self.jetpack,
            kernel=self.kernel,
        )
