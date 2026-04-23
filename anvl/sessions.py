"""Cross-project session overview with usage tracking."""

import json
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import get_projects_dir, get_sessions_dir, load_config
from .parser import (
    DEFAULT_CONTEXT_LIMIT,
    ToolUseRecord,
    compute_churn_from_tools,
    compute_context_tier,
    context_limit_for_model,
    worst_tier,
)

# Simple mtime-based cache for collect_all_sessions
_session_cache: dict = {"summaries": [], "mtime_key": "", "ts": 0.0}
_CACHE_TTL = 3.0  # seconds

# Weighted token costs matching Claude's actual pricing ratios.
# These weights approximate real quota impact.
TOKEN_WEIGHTS = {
    "input": 1.0,
    "cache_read": 0.1,  # Cache reads are ~90% cheaper
    "cache_creation": 1.25,  # Slightly more expensive than regular input
    "output": 5.0,  # Output tokens cost ~5x input
}

# Baseline is computed from the median token cost of turns 3..7 (inclusive).
# Turns 1-2 include the system prompt and handoff reads, so they inflate
# the baseline. Turn 8+ is where normal work starts.
BASELINE_TURN_START = 2  # 0-indexed turn 2 = human turn 3
BASELINE_TURN_END = 7  # 0-indexed turn 6 = human turn 7 (exclusive: slice [2:7])

# Inflation alert thresholds: avg tokens/turn over the last 5 turns divided
# by the median baseline. ANVL's original promise — warn before Claude does
# when a session is burning way more tokens per turn than a fresh one would.
INFLATION_YELLOW = 1.5
INFLATION_RED = 2.5


def compute_inflation_tier(per_turn_tokens: list[int]) -> tuple[str, float, str]:
    """Return (tier, ratio, reason) for tokens-per-turn inflation vs baseline.

    Baseline = median of turns 3..7 (the stable window before context grows
    naturally). Recent = average of the last 5 turns. Below `BASELINE_TURN_END`
    turns the baseline isn't reliable yet — return green.
    """
    if len(per_turn_tokens) < BASELINE_TURN_END:
        return "green", 1.0, f"baseline warming up ({len(per_turn_tokens)} turns)"
    window = per_turn_tokens[BASELINE_TURN_START:BASELINE_TURN_END]
    if not window:
        return "green", 1.0, "no baseline data"
    baseline = statistics.median(window)
    if baseline <= 0:
        return "green", 1.0, "no baseline data"
    recent_window = per_turn_tokens[-5:]
    if not recent_window:
        return "green", 1.0, "no recent data"
    recent_avg = sum(recent_window) / len(recent_window)
    ratio = recent_avg / baseline
    if ratio >= INFLATION_RED:
        tier = "red"
    elif ratio >= INFLATION_YELLOW:
        tier = "yellow"
    else:
        tier = "green"
    reason = f"tokens/turn inflated {ratio:.1f}x over baseline ({int(baseline):,} → {int(recent_avg):,})"
    return tier, round(ratio, 2), reason


def _resolve_context_limit(model_id: str, per_turn_context: list[int]) -> int:
    """Resolve the per-session context window limit.

    Priority:
    1. User's explicit config value (`context_limit` in ~/.anvl/config.json)
       wins when present and not equal to the internal default.
    2. Model-derived limit from the model ID seen in the JSONL.
    3. If we observe a turn with context > 200K, we are definitely on a 1M
       variant — bump accordingly. This self-corrects when the ID alone
       was ambiguous.
    """
    cfg = load_config()
    explicit = cfg.get("context_limit")
    if explicit:
        try:
            val = int(explicit)
            if val and val != DEFAULT_CONTEXT_LIMIT:
                return val
        except (TypeError, ValueError):
            pass

    limit = context_limit_for_model(model_id)
    if per_turn_context and max(per_turn_context) > DEFAULT_CONTEXT_LIMIT:
        limit = max(limit, 1_000_000)
    return limit


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
    per_turn_tokens: list[int] = field(default_factory=list)

    # Churn metric (primary health signal)
    churn_score: float = 0.0
    redundant_read_count: int = 0
    productive_edit_count: int = 0
    churn_tier: str = "green"
    churn_reason: str = ""
    most_reread_files: list[tuple[str, int]] = field(default_factory=list)

    # Context-window pressure (parallel signal)
    context_tokens: int = 0  # last turn's total input (what the model sees now)
    context_pct: float = 0.0
    context_tier: str = "green"
    context_reason: str = ""
    context_limit: int = 200_000  # limit used to classify this session
    model: str = ""  # most recent assistant model id seen in the JSONL

    # Inflation pressure (ANVL's original signal — per-turn cost vs baseline)
    inflation_tier: str = "green"
    inflation_reason: str = ""

    # Combined health — worst of churn, context, and inflation tiers
    health_tier: str = "green"
    health_reason: str = ""

    @property
    def session_baseline_tpt(self) -> int:
        """Median tokens/turn in turns 3..7 (1-indexed).

        Robust anchor that excludes warmup turns (system prompt, handoff read)
        and the long tail where context has grown naturally.
        """
        tokens = self.per_turn_tokens
        if len(tokens) < BASELINE_TURN_START + 1:
            return 0
        window = tokens[BASELINE_TURN_START:BASELINE_TURN_END]
        if not window:
            return 0
        return int(statistics.median(window))

    @property
    def inflation_ratio(self) -> float:
        """Recent tpt / baseline tpt. Drives the inflation alert tier."""
        baseline = self.session_baseline_tpt
        if baseline <= 0 or len(self.per_turn_tokens) < BASELINE_TURN_END:
            return 1.0
        recent = self.per_turn_tokens[-5:]
        if not recent:
            return 1.0
        return round((sum(recent) / len(recent)) / baseline, 1)

    @property
    def efficiency(self) -> str:
        """Backwards-compat alias for health_tier (used by monitor UI colors)."""
        return self.health_tier

    @property
    def weighted_cost(self) -> float:
        """Weighted token cost approximating real quota impact."""
        return (
            self.raw_input * TOKEN_WEIGHTS["input"]
            + self.cache_read * TOKEN_WEIGHTS["cache_read"]
            + self.cache_creation * TOKEN_WEIGHTS["cache_creation"]
            + self.total_output * TOKEN_WEIGHTS["output"]
        )


def _quick_session_stats(jsonl_path: Path) -> dict:
    """Fast single-pass session parser for token totals + tool uses per turn.

    Returns a dict with token breakdown, per-turn totals, and per-turn
    tool_uses lists (for churn computation).
    """
    totals = {
        "input": 0,  # raw input_tokens
        "cache_read": 0,
        "cache_creation": 0,
        "output": 0,
        "turns": 0,
        "per_turn_tokens": [],  # total tokens per user turn
        "per_turn_context": [],  # input + cache_read + cache_creation per turn (what model saw)
        "tools_per_turn": [],  # list[list[ToolUseRecord]]
        "model": "",  # most recent assistant model id seen
    }

    request_usage: dict[str, dict] = {}
    current_turn_direct = 0
    turn_request_usage: dict[str, int] = {}
    turn_request_context: dict[str, int] = {}  # per-request context size (input+cache)
    current_turn_direct_context = 0
    current_tool_uses: list[ToolUseRecord] = []
    in_turn = False

    def _finalize_turn():
        nonlocal current_turn_direct, turn_request_usage, current_tool_uses
        nonlocal current_turn_direct_context, turn_request_context
        turn_total = current_turn_direct + sum(turn_request_usage.values())
        turn_context = current_turn_direct_context + max(turn_request_context.values(), default=0)
        if turn_total > 0:
            totals["per_turn_tokens"].append(turn_total)
            totals["per_turn_context"].append(turn_context)
            totals["tools_per_turn"].append(current_tool_uses)
        current_turn_direct = 0
        current_turn_direct_context = 0
        turn_request_usage = {}
        turn_request_context = {}
        current_tool_uses = []

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
                    is_tool_result = any(isinstance(c, dict) and "tool_use_id" in c for c in content)
                    if not is_tool_result:
                        if in_turn:
                            _finalize_turn()
                        in_turn = True
                        totals["turns"] += 1

                elif rtype == "assistant":
                    msg = record.get("message", {})
                    model_id = msg.get("model", "")
                    if model_id:
                        totals["model"] = model_id
                    usage = msg.get("usage", {})
                    if usage:
                        inp = usage.get("input_tokens", 0)
                        cr = usage.get("cache_read_input_tokens", 0)
                        cc = usage.get("cache_creation_input_tokens", 0)
                        out = usage.get("output_tokens", 0)
                        total = inp + cr + cc + out
                        context_size = inp + cr + cc  # what the model saw
                        request_id = record.get("requestId", "")
                        if request_id:
                            request_usage[request_id] = usage
                            turn_request_usage[request_id] = total
                            turn_request_context[request_id] = context_size
                        else:
                            totals["input"] += inp
                            totals["cache_read"] += cr
                            totals["cache_creation"] += cc
                            totals["output"] += out
                            current_turn_direct += total
                            current_turn_direct_context += context_size

                    # Extract tool uses for churn
                    for block in msg.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            name = block.get("name", "")
                            inp = block.get("input", {})
                            current_tool_uses.append(
                                ToolUseRecord(
                                    name=name,
                                    file_path=inp.get("file_path") or inp.get("path") or inp.get("pattern"),
                                    command=inp.get("command"),
                                )
                            )
    except OSError:
        pass

    if in_turn:
        _finalize_turn()

    for usage in request_usage.values():
        totals["input"] += usage.get("input_tokens", 0)
        totals["cache_read"] += usage.get("cache_read_input_tokens", 0)
        totals["cache_creation"] += usage.get("cache_creation_input_tokens", 0)
        totals["output"] += usage.get("output_tokens", 0)

    return totals


# Backwards-compat alias: older code paths may still call _quick_token_sum.
_quick_token_sum = _quick_session_stats


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
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True, text=True, timeout=3
            )
            output = result.stdout.lower()
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


def _build_summary_from_stats(
    session_id: str,
    project: str,
    cwd: str,
    ai_title: str,
    pid: int,
    started_at: datetime,
    is_active: bool,
    stats: dict,
) -> SessionSummary:
    """Build a SessionSummary with churn + context + inflation pressure."""
    churn = compute_churn_from_tools(stats.get("tools_per_turn", []))

    per_turn_context = stats.get("per_turn_context", [])
    last_ctx = per_turn_context[-1] if per_turn_context else 0
    model_id = stats.get("model", "")
    resolved_limit = _resolve_context_limit(model_id, per_turn_context)
    ctx_tier, ctx_pct, ctx_reason = compute_context_tier(last_ctx, limit=resolved_limit)

    per_turn_tokens = stats.get("per_turn_tokens", [])
    infl_tier, _infl_ratio, infl_reason = compute_inflation_tier(per_turn_tokens)

    combined_tier = worst_tier(worst_tier(churn.health_tier, ctx_tier), infl_tier)
    if combined_tier == "green":
        combined_reason = churn.health_reason or ctx_reason
    elif combined_tier == churn.health_tier:
        combined_reason = churn.health_reason
    elif combined_tier == ctx_tier:
        combined_reason = ctx_reason
    else:
        combined_reason = infl_reason

    return SessionSummary(
        session_id=session_id,
        project=project,
        cwd=cwd,
        ai_title=ai_title or "Untitled",
        pid=pid,
        started_at=started_at,
        is_active=is_active,
        total_input=stats["input"] + stats["cache_read"] + stats["cache_creation"],
        total_output=stats["output"],
        cache_read=stats["cache_read"],
        cache_creation=stats["cache_creation"],
        raw_input=stats["input"],
        turns=stats["turns"],
        per_turn_tokens=stats["per_turn_tokens"],
        churn_score=churn.churn_score,
        redundant_read_count=churn.redundant_read_count,
        productive_edit_count=churn.productive_edit_count,
        churn_tier=churn.health_tier,
        churn_reason=churn.health_reason,
        most_reread_files=churn.most_reread_files,
        context_tokens=last_ctx,
        context_pct=ctx_pct,
        context_tier=ctx_tier,
        context_reason=ctx_reason,
        context_limit=resolved_limit,
        model=model_id,
        inflation_tier=infl_tier,
        inflation_reason=infl_reason,
        health_tier=combined_tier,
        health_reason=combined_reason,
    )


def collect_all_sessions() -> list[SessionSummary]:
    """Collect all sessions across all projects.

    Uses mtime-based caching to avoid re-parsing when nothing changed.
    """
    global _session_cache
    sessions_dir = get_sessions_dir()
    projects_dir = get_projects_dir()

    if not projects_dir.exists():
        return []

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
                started_at = datetime.fromtimestamp(jsonl_file.stat().st_mtime, tz=timezone.utc)

            is_active = bool(pid) and _is_process_running(pid)
            ai_title = _get_ai_title(jsonl_file)
            stats = _quick_session_stats(jsonl_file)
            project_name = _extract_project_name(cwd) if cwd else project_dir.name

            summaries.append(
                _build_summary_from_stats(
                    session_id=session_id,
                    project=project_name,
                    cwd=cwd,
                    ai_title=ai_title,
                    pid=pid,
                    started_at=started_at,
                    is_active=is_active,
                    stats=stats,
                )
            )

    summaries.sort(key=lambda s: (not s.is_active, -s.started_at.timestamp()))

    _session_cache["summaries"] = summaries
    _session_cache["mtime_key"] = mtime_key
    _session_cache["ts"] = now

    return summaries


def compute_window_usage(
    summaries: list[SessionSummary],
    window_hours: int = 5,
) -> tuple[float, float, datetime | None]:
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
    """Compute session rotation savings using per-session baselines.

    Waste = how many tokens a session consumed above its own baseline × turns.
    Savings = when rotating to a fresh session, how much cheaper the new
    session's early turns are vs the previous session's tail.
    """
    from collections import defaultdict

    by_project: dict[str, list[SessionSummary]] = defaultdict(list)
    for s in summaries:
        if s.turns >= 5 and s.per_turn_tokens:
            by_project[s.project].append(s)
    for proj in by_project:
        by_project[proj].sort(key=lambda s: s.started_at)

    total_wasted = 0
    total_saved = 0

    for _proj, sess_list in by_project.items():
        for i, s in enumerate(sess_list):
            baseline = s.session_baseline_tpt
            if baseline > 0:
                ideal = baseline * s.turns
                total_wasted += max(0, sum(s.per_turn_tokens) - ideal)

            if i > 0:
                prev = sess_list[i - 1]
                if not prev.per_turn_tokens:
                    continue
                tail = prev.per_turn_tokens[-5:]
                prev_avg = sum(tail) / len(tail)
                head = s.per_turn_tokens[:5]
                fresh_avg = sum(head) / len(head)
                saving_per_turn = max(0, prev_avg - fresh_avg)
                benefited = min(s.turns, 10)
                total_saved += int(saving_per_turn * benefited)

    return {
        "total_wasted": int(total_wasted),
        "saved_tokens": int(total_saved),
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
