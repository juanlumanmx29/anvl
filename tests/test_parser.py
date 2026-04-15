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


def test_compute_churn_empty():
    from anvl.parser import compute_churn_from_tools

    stats = compute_churn_from_tools([])
    assert stats.churn_score == 0.0
    assert stats.health_tier == "green"


def test_compute_churn_warmup():
    from anvl.parser import ToolUseRecord, compute_churn_from_tools

    # < 5 turns always stays green
    tools = [[ToolUseRecord(name="Read", file_path=f"/f{i}.py")] for i in range(3)]
    stats = compute_churn_from_tools(tools)
    assert stats.health_tier == "green"
    assert "warming up" in stats.health_reason


def test_compute_churn_productive():
    from anvl.parser import ToolUseRecord, compute_churn_from_tools

    # 10 productive edits, no redundant reads -> very green
    tools = [
        [ToolUseRecord(name="Read", file_path=f"/f{i}.py"), ToolUseRecord(name="Edit", file_path=f"/f{i}.py")]
        for i in range(10)
    ]
    stats = compute_churn_from_tools(tools)
    assert stats.churn_score == 0.0
    assert stats.health_tier == "green"
    assert stats.productive_edit_count == 10


def test_compute_churn_stuck():
    from anvl.parser import ToolUseRecord, compute_churn_from_tools

    # Session reads the same 3 files over and over with few edits
    tools = []
    for _ in range(10):
        tools.append(
            [
                ToolUseRecord(name="Read", file_path="/a.py"),
                ToolUseRecord(name="Read", file_path="/b.py"),
                ToolUseRecord(name="Read", file_path="/c.py"),
            ]
        )
    # Add a single edit so low_activity floor doesn't kick in
    tools[-1].append(ToolUseRecord(name="Edit", file_path="/a.py"))

    stats = compute_churn_from_tools(tools)
    assert stats.churn_score > 3.0  # very high churn
    assert stats.health_tier == "critical"


def test_compute_churn_low_activity_floor():
    from anvl.parser import ToolUseRecord, compute_churn_from_tools

    # 10 turns with only 1 redundant read and 1 edit — insufficient sample
    tools: list[list[ToolUseRecord]] = [[] for _ in range(10)]
    tools[0].append(ToolUseRecord(name="Read", file_path="/a.py"))
    tools[1].append(ToolUseRecord(name="Read", file_path="/a.py"))  # redundant
    tools[2].append(ToolUseRecord(name="Edit", file_path="/a.py"))

    stats = compute_churn_from_tools(tools)
    assert stats.churn_score == 0.0
    assert stats.health_tier == "green"
