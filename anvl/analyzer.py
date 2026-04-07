"""Waste factor computation and session metrics analysis."""

from dataclasses import dataclass, field

from .parser import SessionData, TokenUsage, Turn


@dataclass
class TurnMetrics:
    turn_index: int = 0
    waste_factor: float = 0.0
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
    current_waste_factor: float = 0.0
    average_waste_factor: float = 0.0
    semaphore: str = "green"
    trend: str = "stable"
    per_turn: list[TurnMetrics] = field(default_factory=list)


def compute_waste(usage: TokenUsage) -> float:
    """Compute waste factor: total_input / max(output, 1)."""
    return usage.total_input / max(usage.output_tokens, 1)


def compute_semaphore(waste: float) -> str:
    """Green < 3x, yellow 3-7x, red > 7x."""
    if waste < 3:
        return "green"
    elif waste <= 7:
        return "yellow"
    return "red"


def compute_trend(per_turn: list[TurnMetrics], window: int = 5) -> str:
    """Compare average waste of last window turns vs previous window."""
    # Filter out tool-only turns
    meaningful = [t for t in per_turn if not t.is_tool_only]
    if len(meaningful) < 4:
        return "stable"

    mid = max(len(meaningful) - window, len(meaningful) // 2)
    recent = meaningful[mid:]
    previous = meaningful[max(0, mid - window):mid]

    if not previous or not recent:
        return "stable"

    avg_recent = sum(t.waste_factor for t in recent) / len(recent)
    avg_prev = sum(t.waste_factor for t in previous) / len(previous)

    ratio = avg_recent / max(avg_prev, 0.1)
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
    meaningful_wastes: list[float] = []

    for turn in session.turns:
        if turn.usage is None:
            continue

        u = turn.usage
        waste = compute_waste(u)
        cumulative_input += u.total_input
        cumulative_output += u.output_tokens

        tm = TurnMetrics(
            turn_index=turn.index,
            waste_factor=waste,
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

        if not turn.is_tool_only:
            meaningful_wastes.append(waste)

    # Current waste: last non-tool-only turn
    for tm in reversed(metrics.per_turn):
        if not tm.is_tool_only:
            metrics.current_waste_factor = tm.waste_factor
            break

    # Average waste (excluding tool-only turns)
    if meaningful_wastes:
        metrics.average_waste_factor = sum(meaningful_wastes) / len(meaningful_wastes)

    metrics.semaphore = compute_semaphore(metrics.current_waste_factor)
    metrics.trend = compute_trend(metrics.per_turn)

    return metrics


def format_tokens(n: int) -> str:
    """Format token count with K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)
