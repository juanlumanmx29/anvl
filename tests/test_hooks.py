"""Tests for anvl.hooks module."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from anvl.hooks import (
    HOOK_COMMANDS,
    _find_anvl_hook_index,
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
