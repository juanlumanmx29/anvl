"""Session metrics analysis — churn-based health model.

The primary health signal is churn: how many redundant file reads happen
relative to productive edits. This replaces the previous waste_factor model
which measured token growth vs a global baseline.
"""

import statistics
from dataclasses import dataclass, field

from .parser import SessionData, TokenUsage, compute_churn, compute_context_tier, worst_tier

# Window size for baseline tpt (turns 3..7 inclusive in 1-indexed terms)
BASELINE_TURN_START = 2  # 0-indexed start
BASELINE_TURN_END = 7  # 0-indexed exclusive end


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
    peak_context: int = 0  # max context window size in this turn
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
    # Churn-based health
    churn_score: float = 0.0
    redundant_read_count: int = 0
    productive_edit_count: int = 0
    churn_tier: str = "green"
    churn_reason: str = ""
    most_reread_files: list[tuple[str, int]] = field(default_factory=list)
    # Context window pressure
    context_tokens: int = 0
    context_pct: float = 0.0
    context_tier: str = "green"
    context_reason: str = ""
    # Combined
    health_tier: str = "green"
    health_reason: str = ""
    # Session-relative baseline (informational)
    baseline_per_turn: int = 0
    current_per_turn: int = 0
    inflation_ratio: float = 1.0
    trend: str = "stable"
    per_turn: list[TurnMetrics] = field(default_factory=list)


def _total_tokens(usage: TokenUsage) -> int:
    return usage.input_tokens + usage.cache_read_input_tokens + usage.cache_creation_input_tokens + usage.output_tokens


def compute_session_baseline(per_turn: list[TurnMetrics]) -> int:
    """Median tokens/turn from turns 3..7 (1-indexed), excluding tool-only turns.

    Stable anchor that avoids system-prompt inflation (turns 1-2) and
    long-session context growth (turns 8+).
    """
    meaningful = [t.total_tokens for t in per_turn if not t.is_tool_only]
    if len(meaningful) < BASELINE_TURN_START + 1:
        return 0
    window = meaningful[BASELINE_TURN_START:BASELINE_TURN_END]
    if not window:
        return 0
    return int(statistics.median(window))


def compute_inflation_ratio(per_turn: list[TurnMetrics]) -> tuple[float, int, int]:
    """Returns (inflation_ratio, baseline_per_turn, current_per_turn).

    Inflation = median(last 5 turns) / session baseline.  Informational only —
    not used to trigger alerts.
    """
    baseline = compute_session_baseline(per_turn)
    meaningful = [t.total_tokens for t in per_turn if not t.is_tool_only]
    if not meaningful:
        return 1.0, baseline, 0

    tail = meaningful[-5:]
    current = int(sum(tail) / len(tail)) if tail else 0

    if baseline == 0:
        return 1.0, baseline, current
    return round(current / baseline, 1), baseline, current


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


def analyze_session(session: SessionData) -> SessionMetrics:
    """Compute full metrics for a session using the churn model."""
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
            peak_context=turn.peak_context,
            is_tool_only=turn.is_tool_only,
            timestamp=turn.timestamp,
        )
        metrics.per_turn.append(tm)

        metrics.total_input_tokens += u.total_input
        metrics.total_output_tokens += u.output_tokens
        metrics.total_cache_read += u.cache_read_input_tokens
        metrics.total_cache_creation += u.cache_creation_input_tokens

    # Churn
    churn = compute_churn(session.turns)
    metrics.churn_score = churn.churn_score
    metrics.redundant_read_count = churn.redundant_read_count
    metrics.productive_edit_count = churn.productive_edit_count
    metrics.churn_tier = churn.health_tier
    metrics.churn_reason = churn.health_reason
    metrics.most_reread_files = churn.most_reread_files

    # Context pressure: use the last meaningful turn's peak context
    last_context = 0
    for tm in reversed(metrics.per_turn):
        if tm.peak_context > 0:
            last_context = tm.peak_context
            break
    from .config import get_context_limit

    ctx_tier, ctx_pct, ctx_reason = compute_context_tier(last_context, limit=get_context_limit())
    metrics.context_tokens = last_context
    metrics.context_pct = ctx_pct
    metrics.context_tier = ctx_tier
    metrics.context_reason = ctx_reason

    # Combined
    combined = worst_tier(churn.health_tier, ctx_tier)
    metrics.health_tier = combined
    if combined == churn.health_tier and churn.health_tier != "green":
        metrics.health_reason = churn.health_reason
    elif combined == ctx_tier and ctx_tier != "green":
        metrics.health_reason = ctx_reason
    else:
        metrics.health_reason = churn.health_reason or ctx_reason

    # Inflation (informational)
    inflation, baseline, current = compute_inflation_ratio(metrics.per_turn)
    metrics.inflation_ratio = inflation
    metrics.baseline_per_turn = baseline
    metrics.current_per_turn = current

    metrics.trend = compute_trend(metrics.per_turn)

    return metrics


def format_tokens(n: int) -> str:
    """Format token count with K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)
