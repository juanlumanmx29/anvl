"""Cross-project session overview with usage tracking."""

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import CLAUDE_HOME, get_projects_dir, get_sessions_dir, load_config

# Simple mtime-based cache for collect_all_sessions
_session_cache: dict = {"summaries": [], "mtime_key": "", "ts": 0.0}
_CACHE_TTL = 3.0  # seconds

# Weighted token costs matching Claude's actual pricing ratios
# These weights approximate real quota impact
TOKEN_WEIGHTS = {
    "input": 1.0,
    "cache_read": 0.1,       # Cache reads are ~90% cheaper
    "cache_creation": 1.25,  # Slightly more expensive than regular input
    "output": 5.0,           # Output tokens cost ~5x input
}


@dataclass
class SessionSummary:
    session_id: str
    project: str
    cwd: str
    ai_title: str
    pid: int
    started_at: datetime
    is_active: bool
    total_input: int = 0
    total_output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    raw_input: int = 0  # input_tokens only (not cache)
    turns: int = 0

    @property
    def waste_factor(self) -> float:
        """Simple waste: total_input / output."""
        return self.total_input / max(self.total_output, 1)

    @property
    def efficiency(self) -> str:
        """Session health: green/yellow/red based on waste."""
        w = self.waste_factor
        if w < 3:
            return "green"
        elif w <= 7:
            return "yellow"
        return "red"

    @property
    def weighted_cost(self) -> float:
        """Weighted token cost approximating real quota impact."""
        return (
            self.raw_input * TOKEN_WEIGHTS["input"]
            + self.cache_read * TOKEN_WEIGHTS["cache_read"]
            + self.cache_creation * TOKEN_WEIGHTS["cache_creation"]
            + self.total_output * TOKEN_WEIGHTS["output"]
        )


def _quick_token_sum(jsonl_path: Path) -> dict:
    """Fast token counting from JSONL. Returns dict with detailed breakdown."""
    totals = {
        "input": 0,        # raw input_tokens
        "cache_read": 0,
        "cache_creation": 0,
        "output": 0,
        "turns": 0,
    }

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rtype = record.get("type")
                if rtype == "user":
                    content = record.get("message", {}).get("content", [])
                    is_tool_result = any(
                        isinstance(c, dict) and "tool_use_id" in c for c in content
                    )
                    if not is_tool_result:
                        totals["turns"] += 1

                elif rtype == "assistant":
                    usage = record.get("message", {}).get("usage", {})
                    if usage:
                        totals["input"] += usage.get("input_tokens", 0)
                        totals["cache_read"] += usage.get("cache_read_input_tokens", 0)
                        totals["cache_creation"] += usage.get("cache_creation_input_tokens", 0)
                        totals["output"] += usage.get("output_tokens", 0)
    except OSError:
        pass

    return totals


def _get_ai_title(jsonl_path: Path) -> str:
    """Extract ai_title from first few lines of JSONL."""
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i > 20:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if record.get("type") == "ai-title":
                        return record.get("aiTitle", "")
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return ""


def _is_process_running(pid: int) -> bool:
    """Check if a Claude Code process with given PID is running.

    On Windows, PIDs get recycled — we verify the process is actually
    node/claude, not just any random process that inherited the PID.
    """
    try:
        if os.name == "nt":
            import subprocess
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=3
            )
            output = result.stdout.lower()
            # Must be a node.exe or claude process, not a recycled PID
            return str(pid) in output and ("node" in output or "claude" in output)
        else:
            os.kill(pid, 0)
            return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _extract_project_name(cwd: str) -> str:
    """Extract short project name from full cwd path."""
    parts = Path(cwd).parts
    return parts[-1] if parts else cwd


def _build_mtime_key(projects_dir: Path) -> str:
    """Build a lightweight fingerprint from project dirs' mtimes."""
    parts = []
    try:
        for d in sorted(projects_dir.iterdir()):
            if d.is_dir():
                parts.append(f"{d.name}:{d.stat().st_mtime:.0f}")
    except OSError:
        pass
    return "|".join(parts)


def collect_all_sessions() -> list[SessionSummary]:
    """Collect all sessions across all projects.

    Uses mtime-based caching to avoid re-parsing when nothing changed.
    """
    global _session_cache
    sessions_dir = get_sessions_dir()
    projects_dir = get_projects_dir()

    if not projects_dir.exists():
        return []

    # Check cache validity
    now = time.monotonic()
    mtime_key = _build_mtime_key(projects_dir)
    if (
        _session_cache["summaries"]
        and _session_cache["mtime_key"] == mtime_key
        and (now - _session_cache["ts"]) < _CACHE_TTL
    ):
        return _session_cache["summaries"]

    summaries = []

    # Build map of active sessions from PID files
    active_pids: dict[str, dict] = {}
    if sessions_dir.exists():
        for pid_file in sessions_dir.glob("*.json"):
            try:
                with open(pid_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sid = data.get("sessionId", "")
                if sid:
                    active_pids[sid] = data
            except (json.JSONDecodeError, OSError):
                continue

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        for jsonl_file in project_dir.glob("*.jsonl"):
            session_id = jsonl_file.stem
            pid_data = active_pids.get(session_id, {})

            cwd = pid_data.get("cwd", "")
            pid = pid_data.get("pid", 0)
            started_ts = pid_data.get("startedAt", 0)

            if started_ts:
                started_at = datetime.fromtimestamp(started_ts / 1000, tz=timezone.utc)
            else:
                started_at = datetime.fromtimestamp(
                    jsonl_file.stat().st_mtime, tz=timezone.utc
                )

            is_active = bool(pid) and _is_process_running(pid)
            ai_title = _get_ai_title(jsonl_file)
            totals = _quick_token_sum(jsonl_file)
            project_name = _extract_project_name(cwd) if cwd else project_dir.name

            summaries.append(SessionSummary(
                session_id=session_id,
                project=project_name,
                cwd=cwd,
                ai_title=ai_title or "Untitled",
                pid=pid,
                started_at=started_at,
                is_active=is_active,
                total_input=totals["input"] + totals["cache_read"] + totals["cache_creation"],
                total_output=totals["output"],
                cache_read=totals["cache_read"],
                cache_creation=totals["cache_creation"],
                raw_input=totals["input"],
                turns=totals["turns"],
            ))

    summaries.sort(key=lambda s: (not s.is_active, -s.started_at.timestamp()))

    _session_cache["summaries"] = summaries
    _session_cache["mtime_key"] = mtime_key
    _session_cache["ts"] = now

    return summaries


def compute_window_usage(summaries: list[SessionSummary], window_hours: int = 5) -> tuple[float, float, datetime | None]:
    """Compute weighted token usage within the rolling window.

    Returns (weighted_total, weighted_limit_fraction, window_start).
    """
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=window_hours)

    earliest = None
    weighted_total = 0.0

    for s in summaries:
        if s.started_at >= window_start:
            weighted_total += s.weighted_cost
            if earliest is None or s.started_at < earliest:
                earliest = s.started_at

    return weighted_total, 0.0, earliest


def compute_savings(summaries: list[SessionSummary]) -> dict:
    """Estimate how much quota was saved by handoffs.

    Compares actual usage vs hypothetical single-session usage.
    If a session had been one giant conversation, cache reads would be
    much higher. Splitting into fresh sessions resets the context.
    """
    # For sessions with >30 turns, estimate what the waste would have been
    # without handoffs: each additional turn reads more and more cache
    total_actual_weighted = 0.0
    total_hypothetical_weighted = 0.0

    for s in summaries:
        actual = s.weighted_cost
        total_actual_weighted += actual

        if s.turns > 10:
            # Without handoff: cache reads would grow linearly
            # Estimate: baseline cost per turn (first turns) vs inflated cost
            avg_per_turn = actual / max(s.turns, 1)
            # In a fresh session, cost per turn is ~baseline
            # In inflated session, later turns cost 3-10x more
            # Rough estimate: rotating every 30 turns saves ~40% of cache reads
            hypothetical = actual * (1 + (s.turns / 30) * 0.4)
            total_hypothetical_weighted += hypothetical
        else:
            total_hypothetical_weighted += actual

    saved = total_hypothetical_weighted - total_actual_weighted
    pct_saved = (saved / max(total_hypothetical_weighted, 1)) * 100

    return {
        "actual_weighted": total_actual_weighted,
        "hypothetical_weighted": total_hypothetical_weighted,
        "saved_weighted": max(0, saved),
        "pct_saved": max(0, pct_saved),
    }


def get_reset_info(config: dict, window_start: datetime | None = None) -> tuple[str, str]:
    """Calculate time until quota reset for rolling window."""
    window_hours = config.get("window_hours", 5)
    now = datetime.now(timezone.utc)

    if window_start is None:
        return "Fresh", "No active window"

    reset_at = window_start + timedelta(hours=window_hours)

    if now >= reset_at:
        return "Fresh", "Window expired"

    delta = reset_at - now
    hours = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)

    time_remaining = f"{hours}h {minutes}m"
    reset_time = reset_at.astimezone().strftime("%H:%M %Z")

    return time_remaining, reset_time
