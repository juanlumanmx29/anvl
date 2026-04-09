"""Waste factor computation and session metrics analysis.

Waste factor follows the Clauditor approach:
  waste = avg_tokens_per_turn(last 5) / avg_tokens_per_turn(first 5)

This measures how much the session has *grown* compared to its baseline,
not an abstract input/output ratio.  A fresh session starts at 1x.
"""

from dataclasses import dataclass, field

from .parser import SessionData, TokenUsage

# Window size for baseline and current averages
BASELINE_WINDOW = 5


@dataclass
class TurnMetrics:
    turn_index: int = 0
    total_tokens: int = 0
    total_input: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    input_tokens: int = 0
    cumulative_input: int = 0
    cumulative_output: int = 0
    is_tool_only: bool = False
    timestamp: str = ""


@dataclass
class SessionMetrics:
    session_id: str = ""
    ai_title: str = ""
    turn_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read: int = 0
    total_cache_creation: int = 0
    waste_factor: float = 1.0
    baseline_per_turn: int = 0
    current_per_turn: int = 0
    health_pct: int = 100
    semaphore: str = "green"
    trend: str = "stable"
    per_turn: list[TurnMetrics] = field(default_factory=list)


def _total_tokens(usage: TokenUsage) -> int:
    """Total tokens for a turn (all input categories + output)."""
    return usage.input_tokens + usage.cache_read_input_tokens + usage.cache_creation_input_tokens + usage.output_tokens


def compute_waste_factor(
    per_turn: list[TurnMetrics],
    window: int = BASELINE_WINDOW,
    calibrated_baseline: int | None = None,
    growth_curve: dict | None = None,
) -> tuple[float, int, int]:
    """Growth-aware waste: max(relative, absolute).

    Signal A (relative): actual growth vs p75 historical growth curve.
    Signal B (absolute): current cost vs fresh session cost.

    Returns (waste_factor, baseline_per_turn, current_per_turn).
    With < window turns, waste is always 1.0.
    """
    meaningful = [t for t in per_turn if not t.is_tool_only]
    if len(meaningful) < window:
        if meaningful:
            avg = sum(t.total_tokens for t in meaningful) // len(meaningful)
            return 1.0, avg, avg
        return 1.0, 0, 0

    session_baseline = min(t.total_tokens for t in meaningful[:window])
    baseline = calibrated_baseline if calibrated_baseline else session_baseline
    effective_bl = max(session_baseline, baseline) if session_baseline and baseline else (session_baseline or baseline)

    current_avg = sum(t.total_tokens for t in meaningful[-window:]) // window
    turn_idx = len(meaningful) - 1

    if effective_bl == 0:
        return 1.0, 0, current_avg

    # Signal A: relative waste vs growth curve
    growth_p75 = (growth_curve or {}).get("growth_p75", [])
    if growth_p75:
        idx = min(turn_idx, len(growth_p75) - 1)
        expected = max(growth_p75[idx], 1.0)
        actual_growth = current_avg / effective_bl
        relative = actual_growth / expected
    else:
        relative = current_avg / effective_bl

    # Signal B: absolute waste vs fresh session cost
    fresh = (growth_curve or {}).get("fresh_cost_p50", 0) or effective_bl
    absolute = current_avg / fresh if fresh > 0 else 1.0

    waste = max(1.0, round(max(relative, absolute), 1))
    return waste, effective_bl, current_avg


def compute_health_pct(waste: float, turns: int = 0, threshold: float = 15.0) -> int:
    """Session health as percentage (0-100).

    Maps waste linearly from 1x (100%) to threshold (0%).
    Sessions with fewer than 5 turns always return 100%.
    """
    if turns < BASELINE_WINDOW:
        return 100
    if waste <= 1.0:
        return 100
    if waste >= threshold:
        return 0
    # Linear interpolation: 1x→100%, threshold→0%
    return max(0, min(100, int(100 * (threshold - waste) / (threshold - 1))))


def compute_semaphore(health_pct: int) -> str:
    """Green/yellow/red derived from health percentage."""
    if health_pct >= 50:
        return "green"
    elif health_pct >= 20:
        return "yellow"
    return "red"


def compute_trend(per_turn: list[TurnMetrics], window: int = 5) -> str:
    """Compare average tokens/turn of last window vs previous window."""
    meaningful = [t for t in per_turn if not t.is_tool_only]
    if len(meaningful) < window * 2:
        return "stable"

    recent = meaningful[-window:]
    previous = meaningful[-window * 2 : -window]

    avg_recent = sum(t.total_tokens for t in recent) / len(recent)
    avg_prev = sum(t.total_tokens for t in previous) / len(previous)

    if avg_prev == 0:
        return "stable"

    ratio = avg_recent / avg_prev
    if ratio > 1.3:
        return "rising"
    elif ratio < 0.7:
        return "falling"
    return "stable"


def analyze_session(
    session: SessionData,
    calibrated_baseline: int | None = None,
    growth_curve: dict | None = None,
) -> SessionMetrics:
    """Compute full metrics for a session."""
    metrics = SessionMetrics(
        session_id=session.session_id,
        ai_title=session.ai_title,
        turn_count=len(session.turns),
    )

    cumulative_input = 0
    cumulative_output = 0

    for turn in session.turns:
        if turn.usage is None:
            continue

        u = turn.usage
        total = _total_tokens(u)
        cumulative_input += u.total_input
        cumulative_output += u.output_tokens

        tm = TurnMetrics(
            turn_index=turn.index,
            total_tokens=total,
            total_input=u.total_input,
            output_tokens=u.output_tokens,
            cache_read=u.cache_read_input_tokens,
            cache_creation=u.cache_creation_input_tokens,
            input_tokens=u.input_tokens,
            cumulative_input=cumulative_input,
            cumulative_output=cumulative_output,
            is_tool_only=turn.is_tool_only,
            timestamp=turn.timestamp,
        )
        metrics.per_turn.append(tm)

        metrics.total_input_tokens += u.total_input
        metrics.total_output_tokens += u.output_tokens
        metrics.total_cache_read += u.cache_read_input_tokens
        metrics.total_cache_creation += u.cache_creation_input_tokens

    # Waste factor: current tokens/turn vs baseline tokens/turn
    waste, baseline, current = compute_waste_factor(
        metrics.per_turn, calibrated_baseline=calibrated_baseline, growth_curve=growth_curve
    )
    metrics.waste_factor = waste
    metrics.baseline_per_turn = baseline
    metrics.current_per_turn = current

    metrics.health_pct = compute_health_pct(waste, metrics.turn_count)
    metrics.semaphore = compute_semaphore(metrics.health_pct)
    metrics.trend = compute_trend(metrics.per_turn)

    return metrics


def format_tokens(n: int) -> str:
    """Format token count with K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)
