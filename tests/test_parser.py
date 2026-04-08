"""Tests for anvl.parser module."""

from pathlib import Path

from anvl.parser import parse_session_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_minimal_session():
    session = parse_session_file(FIXTURES / "minimal.jsonl")
    assert session.session_id == "test-minimal-001"
    assert session.ai_title == "Minimal test session"
    assert session.git_branch == "main"
    assert len(session.turns) == 1


def test_minimal_turn_has_usage():
    session = parse_session_file(FIXTURES / "minimal.jsonl")
    turn = session.turns[0]
    assert turn.usage is not None
    assert turn.usage.input_tokens == 10
    assert turn.usage.cache_creation_input_tokens == 500
    assert turn.usage.cache_read_input_tokens == 200
    assert turn.usage.output_tokens == 50


def test_minimal_turn_text():
    session = parse_session_file(FIXTURES / "minimal.jsonl")
    turn = session.turns[0]
    assert "Hello" in turn.user_text
    assert "help" in turn.assistant_text


def test_parse_inflated_session():
    session = parse_session_file(FIXTURES / "inflated.jsonl")
    assert session.session_id == "test-inflated-001"
    assert len(session.turns) == 5
    assert session.git_branch == "dev"


def test_inflated_cache_read_grows():
    session = parse_session_file(FIXTURES / "inflated.jsonl")
    cache_reads = [t.usage.cache_read_input_tokens for t in session.turns if t.usage]
    # Cache read should be growing across turns
    assert cache_reads == sorted(cache_reads)
    assert cache_reads[-1] > cache_reads[0]


def test_parse_malformed_skips_bad_lines():
    session = parse_session_file(FIXTURES / "malformed.jsonl")
    assert session.session_id == "test-malformed-001"
    assert session.ai_title == "Malformed test"
    assert len(session.turns) == 1  # Only one valid user/assistant pair


def test_total_input_property():
    session = parse_session_file(FIXTURES / "minimal.jsonl")
    usage = session.turns[0].usage
    assert usage.total_input == 10 + 500 + 200
