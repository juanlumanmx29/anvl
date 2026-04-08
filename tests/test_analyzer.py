"""Tests for anvl.analyzer module."""

from pathlib import Path

from anvl.analyzer import (
    TurnMetrics,
    analyze_session,
    compute_health_pct,
    compute_semaphore,
    compute_waste_factor,
    format_tokens,
)
from anvl.parser import parse_session_file

FIXTURES = Path(__file__).parent / "fixtures"


def _make_turns(totals: list[int]) -> list[TurnMetrics]:
    """Create TurnMetrics list from total token counts."""
    return [TurnMetrics(turn_index=i, total_tokens=t) for i, t in enumerate(totals)]


def test_waste_factor_fresh_session():
    """A session where all turns cost the same should have waste ~1.0."""
    turns = _make_turns([1000] * 10)
    waste, baseline, current = compute_waste_factor(turns)
    assert waste == 1.0


def test_waste_factor_inflated():
    """Waste should reflect the peak avg / baseline ratio."""
    # First 5 turns: 1000 each. Last 5: 5000 each → 5x waste
    turns = _make_turns([1000] * 5 + [5000] * 5)
    waste, baseline, current = compute_waste_factor(turns)
    assert waste == 5.0
    assert baseline == 1000
    assert current == 5000


def test_waste_factor_too_few_turns():
    """With fewer turns than the window, waste is always 1.0."""
    turns = _make_turns([1000, 2000, 3000])
    waste, baseline, current = compute_waste_factor(turns)
    assert waste == 1.0


def test_health_pct_fresh():
    assert compute_health_pct(1.0, turns=20) == 100


def test_health_pct_critical():
    assert compute_health_pct(10.0, turns=20) == 0


def test_health_pct_mid():
    # waste=5.5 → 100 * (10 - 5.5) / (10 - 1) = 50%
    assert compute_health_pct(5.5, turns=20) == 50


def test_health_young_session():
    """Young sessions (< 5 turns) always return 100%."""
    assert compute_health_pct(8.0, turns=3) == 100


def test_semaphore_green():
    assert compute_semaphore(100) == "green"
    assert compute_semaphore(60) == "green"


def test_semaphore_yellow():
    assert compute_semaphore(59) == "yellow"
    assert compute_semaphore(30) == "yellow"


def test_semaphore_red():
    assert compute_semaphore(29) == "red"
    assert compute_semaphore(0) == "red"


def test_analyze_minimal_session():
    session = parse_session_file(FIXTURES / "minimal.jsonl")
    metrics = analyze_session(session)
    assert metrics.turn_count == 1
    assert metrics.semaphore == "green"  # 1 turn → young session → 100% health


def test_analyze_inflated_session():
    session = parse_session_file(FIXTURES / "inflated.jsonl")
    metrics = analyze_session(session)
    assert metrics.turn_count == 5
    assert len(metrics.per_turn) == 5
    assert metrics.waste_factor >= 1.0


def test_format_tokens():
    assert format_tokens(500) == "500"
    assert format_tokens(1500) == "1.5K"
    assert format_tokens(1_500_000) == "1.5M"
