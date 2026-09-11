"""Low-frequency reader for the local Codex app-server usage endpoint."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import logging
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import threading
import time
from typing import Any


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageWindow:
    label: str = "USAGE"
    used_percent: int = 0
    resets_at: float = 0.0


@dataclass(frozen=True)
class CodexUsage:
    name: str = "CODEX"
    plan: str = ""
    windows: tuple[UsageWindow, ...] = ()
    credits: str = ""
    reset_credits: int = 0
    ordinary_allowed: bool = True
    limit_reached: str = ""
    updated_at: float = 0.0
    error: str = "waiting for Codex"
    daily_allowance: float = 0.0
    daily_used: float = 0.0
    daily_percent: float = 0.0
    daily_resets_at: float = 0.0


def _window_label(window: dict[str, Any], fallback: str) -> str:
    minutes = window.get("windowDurationMins")
    if minutes == 300:
        return "5 HOUR"
    if minutes == 10080:
        return "WEEKLY"
    if isinstance(minutes, (int, float)) and minutes > 0:
        if minutes % 1440 == 0:
            return f"{int(minutes // 1440)} DAY"
        if minutes % 60 == 0:
            return f"{int(minutes // 60)} HOUR"
    return fallback.upper()


def parse_usage(result: dict[str, Any], now: float | None = None) -> CodexUsage:
    """Reduce the app-server response to fields safe and useful on the LCD."""
    buckets = result.get("rateLimitsByLimitId") or {}
    snapshot = buckets.get("codex") if isinstance(buckets, dict) else None
    if snapshot is None and isinstance(buckets, dict) and buckets:
        snapshot = next(iter(buckets.values()))
    snapshot = snapshot or result.get("rateLimits") or {}

    windows = []
    for key, fallback in (("primary", "PRIMARY"), ("secondary", "SECONDARY")):
        window = snapshot.get(key)
        if not window:
            continue
        windows.append(UsageWindow(
            label=_window_label(window, fallback),
            used_percent=max(0, min(100, int(window.get("usedPercent", 0)))),
            resets_at=float(window.get("resetsAt") or 0),
        ))

    credits = snapshot.get("credits") or {}
    if credits.get("unlimited"):
        credit_text = "UNLIMITED"
    elif credits.get("balance") is not None:
        credit_text = str(credits["balance"])
    elif credits.get("hasCredits"):
        credit_text = "AVAILABLE"
    else:
        credit_text = "NONE"

    plan = str(snapshot.get("planType") or "")
    plan = plan.removeprefix("self_serve_").replace("_", " ").upper()
    reset_credits = result.get("rateLimitResetCredits") or {}
    reached = str(snapshot.get("rateLimitReachedType") or "")
    return CodexUsage(
        name=str(snapshot.get("limitName") or snapshot.get("limitId") or "CODEX").upper(),
        plan=plan,
        windows=tuple(windows),
        credits=credit_text,
        reset_credits=max(0, int(reset_credits.get("availableCount") or 0)),
        ordinary_allowed=bool(result.get("ordinaryUsageAllowed", True)),
        limit_reached=reached.upper(),
        updated_at=time.time() if now is None else now,
        error="",
    )


class DailyBudgetTracker:
    """Persist and calculate an even 24-hour budget for the weekly window."""

    def __init__(self, path: Path | None = None):
        state_root = Path(
            os.environ.get(
                "JETSON_PITFT_STATE_DIR",
                Path.home() / ".local/state/jetson-stats-pitft",
            )
        )
        self.path = path or state_root / "codex-daily-budget.json"
        self._state = self._load()

    def _load(self) -> dict[str, float]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            return {key: float(value) for key, value in data.items()}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self._state, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            os.replace(temporary, self.path)
        except OSError as exc:
            LOG.warning("Could not persist Codex daily budget: %s", exc)

    def apply(self, usage: CodexUsage, now: float | None = None) -> CodexUsage:
        """Attach today's spend versus an evenly divided remaining allowance."""
        current_time = time.time() if now is None else now
        weekly = next((window for window in usage.windows if window.label == "WEEKLY"), None)
        if weekly is None or weekly.resets_at <= current_time:
            return usage

        state = self._state
        same_window = abs(state.get("weekly_resets_at", 0) - weekly.resets_at) < 1
        in_period = current_time < state.get("daily_resets_at", 0)
        monotonic_usage = weekly.used_percent >= state.get("used_at_start", 0)
        if not (same_window and in_period and monotonic_usage):
            remaining_days = max(1.0, (weekly.resets_at - current_time) / 86400.0)
            allowance = max(0.0, 100.0 - weekly.used_percent) / remaining_days
            state = {
                "weekly_resets_at": weekly.resets_at,
                "daily_started_at": current_time,
                "daily_resets_at": min(weekly.resets_at, current_time + 86400.0),
                "used_at_start": float(weekly.used_percent),
                "daily_allowance": allowance,
            }
            self._state = state
            self._save()

        daily_used = max(0.0, weekly.used_percent - state["used_at_start"])
        allowance = state["daily_allowance"]
        daily_percent = 100.0 * daily_used / allowance if allowance > 0 else 100.0
        return replace(
            usage,
            daily_allowance=allowance,
            daily_used=daily_used,
            daily_percent=daily_percent,
            daily_resets_at=state["daily_resets_at"],
        )


class CodexUsageReader:
    """Keep one app-server alive and refresh account usage once per minute."""

    def __init__(self, interval: float = 60.0):
        self.interval = interval
        self._latest = CodexUsage()
        self._lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._budget = DailyBudgetTracker()

    @staticmethod
    def _codex_binary() -> str:
        configured = os.environ.get("CODEX_BIN")
        if configured and os.access(configured, os.X_OK):
            return configured
        discovered = shutil.which("codex")
        if discovered:
            return discovered
        user_binary = Path.home() / ".local/bin/codex"
        if os.access(user_binary, os.X_OK):
            return str(user_binary)
        raise FileNotFoundError("Codex executable not found")

    @staticmethod
    def _send(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()

    @staticmethod
    def _receive(
        process: subprocess.Popen[str], request_id: int, timeout: float = 20.0
    ) -> dict[str, Any]:
        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                events = selector.select(max(0, deadline - time.monotonic()))
                if not events:
                    break
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError("Codex app server stopped")
                message = json.loads(line)
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message.get("result") or {}
        finally:
            selector.close()
        raise TimeoutError("Codex usage request timed out")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="codex-usage", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_session()
            except (OSError, RuntimeError, TimeoutError, json.JSONDecodeError) as exc:
                if not self._stop.is_set():
                    LOG.warning("Codex usage unavailable: %s", exc)
                    with self._lock:
                        self._latest = CodexUsage(error=str(exc))
                    self._stop.wait(15)

    def _run_session(self) -> None:
        process = subprocess.Popen(
            [self._codex_binary(), "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        with self._process_lock:
            self._process = process
        try:
            self._send(process, {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "jetson-stats-pitft", "version": "0.7.0"}},
            })
            self._receive(process, 1)
            request_id = 2
            while not self._stop.is_set():
                self._send(process, {"id": request_id, "method": "account/rateLimits/read"})
                now = time.time()
                usage = parse_usage(self._receive(process, request_id), now=now)
                usage = self._budget.apply(usage, now=now)
                with self._lock:
                    self._latest = usage
                request_id += 1
                if self._stop.wait(self.interval):
                    break
        finally:
            with self._process_lock:
                self._process = None
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()

    def snapshot(self) -> CodexUsage:
        with self._lock:
            return self._latest

    def stop(self) -> None:
        self._stop.set()
        with self._process_lock:
            process = self._process
        if process and process.poll() is None:
            process.terminate()
        if self._thread:
            self._thread.join(timeout=3)
