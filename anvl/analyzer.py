"""Waste factor computation and session metrics analysis.

Waste factor follows the Clauditor approach:
  waste = avg_tokens_per_turn(last 5) / avg_tokens_per_turn(first 5)

This measures how much the session has *grown* compared to its baseline,
not an abstract input/output ratio.  A fresh session starts at 1x.
"""

from dataclasses import dataclass, field

from .parser import SessionData, TokenUsage, Turn

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
    return (
        usage.input_tokens
        + usage.cache_read_input_tokens
        + usage.cache_creation_input_tokens
        + usage.output_tokens
    )


def compute_waste_factor(per_turn: list[TurnMetrics], window: int = BASELINE_WINDOW) -> tuple[float, int, int]:
    """Compute waste as current_avg / baseline_min.

    Baseline is the MINIMUM tokens/turn from the first `window` turns.
    This represents the "fresh session" cost — the cheapest a turn can be
    in this environment.  Using min instead of avg prevents inflated starts
    (e.g., reading handoff.md) from masking real waste.

    Returns (waste_factor, baseline_per_turn, current_per_turn).
    With < window turns, waste is always 1.0.
    """
    meaningful = [t for t in per_turn if not t.is_tool_only]
    if len(meaningful) < window:
        if meaningful:
            avg = sum(t.total_tokens for t in meaningful) // len(meaningful)
            return 1.0, avg, avg
        return 1.0, 0, 0

    baseline_min = min(t.total_tokens for t in meaningful[:window])

    if baseline_min == 0:
        current_avg = sum(t.total_tokens for t in meaningful[-window:]) // window
        return 1.0, 0, current_avg

    # Peak waste: check every possible window position, keep the worst.
    # Health should never improve — once inflated, it stays inflated.
    peak_waste = 1.0
    peak_avg = baseline_min
    for i in range(len(meaningful) - window + 1):
        w = meaningful[i:i + window]
        avg = sum(t.total_tokens for t in w) // len(w)
        w_factor = avg / baseline_min
        if w_factor > peak_waste:
            peak_waste = w_factor
            peak_avg = avg

    current_avg = sum(t.total_tokens for t in meaningful[-window:]) // window
    # Use peak waste but show current avg for display
    waste = round(peak_waste, 1)
    return max(1.0, waste), baseline_min, current_avg


def compute_health_pct(waste: float, turns: int = 0, threshold: float = 10.0) -> int:
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
    if health_pct >= 60:
        return "green"
    elif health_pct >= 30:
        return "yellow"
    return "red"


def compute_trend(per_turn: list[TurnMetrics], window: int = 5) -> str:
    """Compare average tokens/turn of last window vs previous window."""
    meaningful = [t for t in per_turn if not t.is_tool_only]
    if len(meaningful) < window * 2:
        return "stable"

    recent = meaningful[-window:]
    previous = meaningful[-window * 2:-window]

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


def analyze_session(session: SessionData) -> SessionMetrics:
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
    waste, baseline, current = compute_waste_factor(metrics.per_turn)
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
