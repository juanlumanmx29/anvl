"""Tests for anvl.handoff module."""

import tempfile
from pathlib import Path

from anvl.analyzer import analyze_session
from anvl.handoff import (
    extract_files_touched,
    extract_session_summary,
    generate_handoff,
)
from anvl.parser import parse_session_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_summary_minimal():
    session = parse_session_file(FIXTURES / "minimal.jsonl")
    summary = extract_session_summary(session)
    assert "Minimal test session" in summary
    assert "Hello" in summary


def test_extract_files_touched_empty():
    session = parse_session_file(FIXTURES / "minimal.jsonl")
    files = extract_files_touched(session)
    # Minimal session has no tool uses
    assert len(files) == 0


def test_generate_handoff_creates_file():
    session = parse_session_file(FIXTURES / "inflated.jsonl")
    metrics = analyze_session(session)

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "handoff.md"
        generate_handoff(session, metrics, output)
        assert output.exists()

        content = output.read_text(encoding="utf-8")
        assert "# ANVL Handoff" in content
        assert "Inflated test session" in content
        assert "Turns: 5" in content
        assert "## Session summary" in content
        assert "## Technical context" in content
        assert "Branch: dev" in content


def test_handoff_malformed_session():
    session = parse_session_file(FIXTURES / "malformed.jsonl")
    metrics = analyze_session(session)

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "handoff.md"
        generate_handoff(session, metrics, output)
        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "Malformed test" in content
