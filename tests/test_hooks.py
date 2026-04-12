"""Tests for anvl.hooks module."""

import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from anvl.hooks import (
    HOOK_COMMANDS,
    _find_anvl_hook_index,
    hook_entrypoint,
    install_hook,
    uninstall_hook,
)

# Use the PostToolUse command as reference for tests
POST_TOOL_CMD = HOOK_COMMANDS["PostToolUse"]


def _make_settings(hooks=None):
    """Create a temporary settings file and return its path."""
    settings = {}
    if hooks is not None:
        settings["hooks"] = hooks
    tmpdir = tempfile.mkdtemp()
    path = Path(tmpdir) / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    return path


def test_find_anvl_hook_index_empty():
    assert _find_anvl_hook_index([], POST_TOOL_CMD) is None


def test_find_anvl_hook_index_present():
    entries = [
        {"matcher": "", "hooks": [{"type": "command", "command": "other-tool"}]},
        {"matcher": "", "hooks": [{"type": "command", "command": POST_TOOL_CMD}]},
    ]
    assert _find_anvl_hook_index(entries, POST_TOOL_CMD) == 1


def test_install_hook_creates_entry():
    path = _make_settings()
    with patch("anvl.hooks.SETTINGS_PATH", path):
        install_hook()

    settings = json.loads(path.read_text(encoding="utf-8"))
    # Should create entries for all three event types
    for event_type, command in HOOK_COMMANDS.items():
        assert event_type in settings["hooks"]
        event_hooks = settings["hooks"][event_type]
        assert any(h.get("command") == command for entry in event_hooks for h in entry.get("hooks", []))


def test_install_hook_idempotent():
    path = _make_settings()
    with patch("anvl.hooks.SETTINGS_PATH", path):
        install_hook()
        install_hook()  # Second call should not duplicate

    settings = json.loads(path.read_text(encoding="utf-8"))
    for event_type in HOOK_COMMANDS:
        assert len(settings["hooks"][event_type]) == 1


def test_install_preserves_existing_hooks():
    existing = {"PostToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "clauditor check"}]}]}
    path = _make_settings(hooks=existing)
    with patch("anvl.hooks.SETTINGS_PATH", path):
        install_hook()

    settings = json.loads(path.read_text(encoding="utf-8"))
    post_tool_use = settings["hooks"]["PostToolUse"]
    assert len(post_tool_use) == 2
    assert post_tool_use[0]["hooks"][0]["command"] == "clauditor check"
    assert post_tool_use[1]["hooks"][0]["command"] == POST_TOOL_CMD


def test_uninstall_removes_only_anvl():
    existing = {
        "PostToolUse": [
            {"matcher": "", "hooks": [{"type": "command", "command": "clauditor check"}]},
            {"matcher": "", "hooks": [{"type": "command", "command": POST_TOOL_CMD}]},
        ],
        "UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": HOOK_COMMANDS["UserPromptSubmit"]}]},
        ],
        "SessionStart": [
            {"matcher": "", "hooks": [{"type": "command", "command": HOOK_COMMANDS["SessionStart"]}]},
        ],
    }
    path = _make_settings(hooks=existing)
    with patch("anvl.hooks.SETTINGS_PATH", path):
        uninstall_hook()

    settings = json.loads(path.read_text(encoding="utf-8"))
    post_tool_use = settings["hooks"]["PostToolUse"]
    assert len(post_tool_use) == 1
    assert post_tool_use[0]["hooks"][0]["command"] == "clauditor check"


def test_uninstall_no_hook_noop():
    path = _make_settings(hooks={"PostToolUse": []})
    with patch("anvl.hooks.SETTINGS_PATH", path):
        uninstall_hook()  # Should not raise


def _write_inflated_jsonl(path: Path, turns: int = 20, base_tokens: int = 500_000) -> None:
    """Write a jsonl that parses as a heavily inflated session."""
    lines = []
    for i in range(turns):
        lines.append(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "hi"},
        }))
        lines.append(json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "usage": {
                    "input_tokens": base_tokens + i * 50_000,
                    "output_tokens": 500,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_hook_entrypoint_fresh_session_does_not_reuse_stale_log(capsys):
    """Regression: a fresh session whose jsonl does not exist yet must
    NOT fall back to the most-recent log in the project directory.

    Previously, when Claude Code hadn't yet flushed the new session's
    jsonl, the hook reached find_latest_session() and picked up an old
    inflated run, printing a 0%-health banner on a brand-new session.
    """
    tmpdir = Path(tempfile.mkdtemp())
    project_dir = tmpdir / "project"
    project_dir.mkdir()

    # Old inflated session — would trip the alert if chosen.
    _write_inflated_jsonl(project_dir / "old-inflated-session.jsonl")

    # New session id — its jsonl does NOT exist yet (fresh session).
    fresh_session_id = "brand-new-session-0000"
    hook_input = {
        "session_id": fresh_session_id,
        "cwd": str(tmpdir / "cwd"),
        "prompt": "first user message",
    }

    with patch("sys.stdin", io.StringIO(json.dumps(hook_input))), \
         patch("anvl.config.find_project_dir", return_value=project_dir):
        hook_entrypoint(can_block=True)

    captured = capsys.readouterr()
    # Must stay silent — warming up, no alert.
    assert "INFLATED" not in captured.out
    assert "Health" not in captured.out


def test_hook_entrypoint_uses_hook_session_id_when_jsonl_exists(capsys):
    """When the hook_input session_id's jsonl exists and is inflated,
    the hook should alert on THAT session, not silently skip."""
    tmpdir = Path(tempfile.mkdtemp())
    project_dir = tmpdir / "project"
    project_dir.mkdir()

    # Current session jsonl exists and is inflated.
    current_session_id = "current-session-1111"
    _write_inflated_jsonl(project_dir / f"{current_session_id}.jsonl")

    hook_input = {
        "session_id": current_session_id,
        "cwd": str(tmpdir / "cwd"),
        "prompt": "nth user message",
    }

    with patch("sys.stdin", io.StringIO(json.dumps(hook_input))), \
         patch("anvl.config.find_project_dir", return_value=project_dir):
        hook_entrypoint(can_block=True)

    captured = capsys.readouterr()
    # Should have printed an alert banner for the current session.
    assert "ANVL" in captured.out
