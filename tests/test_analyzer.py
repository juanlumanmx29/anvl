"""Tests for anvl.analyzer module."""

from pathlib import Path

from anvl.analyzer import (
    compute_semaphore,
    compute_waste,
    analyze_session,
    format_tokens,
)
from anvl.parser import TokenUsage, parse_session_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_waste_formula():
    usage = TokenUsage(
        input_tokens=10,
        cache_creation_input_tokens=500,
        cache_read_input_tokens=200,
        output_tokens=50,
    )
    # (10 + 500 + 200) / 50 = 14.2
    assert compute_waste(usage) == 14.2


def test_waste_zero_output():
    usage = TokenUsage(input_tokens=100, output_tokens=0)
    # Should not divide by zero
    assert compute_waste(usage) == 100.0


def test_semaphore_green():
    assert compute_semaphore(1.5) == "green"
    assert compute_semaphore(2.9) == "green"


def test_semaphore_yellow():
    assert compute_semaphore(3.0) == "yellow"
    assert compute_semaphore(5.0) == "yellow"
    assert compute_semaphore(7.0) == "yellow"


def test_semaphore_red():
    assert compute_semaphore(7.1) == "red"
    assert compute_semaphore(100.0) == "red"


def test_analyze_minimal_session():
    session = parse_session_file(FIXTURES / "minimal.jsonl")
    metrics = analyze_session(session)
    assert metrics.turn_count == 1
    assert metrics.current_waste_factor == 14.2
    assert metrics.semaphore == "red"


def test_analyze_inflated_session():
    session = parse_session_file(FIXTURES / "inflated.jsonl")
    metrics = analyze_session(session)
    assert metrics.turn_count == 5
    assert len(metrics.per_turn) == 5
    # Last turn: (15 + 5000 + 50000) / 30 = 1833.8x
    assert metrics.current_waste_factor > 1000
    assert metrics.semaphore == "red"


def test_format_tokens():
    assert format_tokens(500) == "500"
    assert format_tokens(1500) == "1.5K"
    assert format_tokens(1_500_000) == "1.5M"
